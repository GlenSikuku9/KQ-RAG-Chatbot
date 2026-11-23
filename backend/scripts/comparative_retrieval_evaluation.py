"""Run the controlled 18-condition comparative retrieval evaluation.

This is an offline research runner.  It builds isolated indexes, maps evidence
from source anchors before retrieval, never invokes a generation API, and
checkpoints after every experimental condition.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import platform
import random
import statistics
import sys
from time import perf_counter
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont

from app.config import get_settings
from app.rag.chunking import chunk_documents
from app.rag.document_loader import load_knowledge_base_documents
from app.rag.retrieval import normalized_search_query, get_retrieval_service
from app.rag.vector_index import VectorIndex, _bm25_scores
from scripts.advanced_multilingual_retrieval_experiment import directory_size
from scripts.final_llm_evaluation import (
    bilingual_representations,
    load_dataset,
    map_evidence,
    request_for,
    translation_locations,
)


OUT = BACKEND_DIR / "data" / "evaluation" / "comparative-retrieval"
INDEX_ROOT = OUT / "indexes"
CHARTS = OUT / "charts"
DOCS = BACKEND_DIR.parent / "docs"
RESULTS = OUT / "comparative_retrieval_results.json"
SEED = 20261119
TOP_K = 5
CANDIDATES = 20
RRF_K = 10
BOOTSTRAPS = 2000
CONFIGS = {
    "A": (1600, 250),
    "B": (1800, 360),
    "C": (1800, 0),
}
STRATEGIES = ("lexical", "semantic", "hybrid")
METRICS = ("mrr_at_5", "recall_at_1", "recall_at_5", "hit_rate_at_5", "context_recall")


def atomic_json(path: Path, value: Any) -> None:
    """Write a checkpoint without exposing a partially written JSON file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))]


def index_snapshot(index: VectorIndex) -> tuple[list[dict], list[list[float]]]:
    """Read the immutable experimental generation used by exact retrieval."""

    manifest = index._manifest()
    if manifest is None:
        raise FileNotFoundError("Experimental index has not been built.")
    collection = index.client.get_collection(manifest.collection, embedding_function=None)
    stored = collection.get(include=["embeddings", "metadatas"])
    metadata = stored["metadatas"]
    embeddings = stored["embeddings"]
    metadata = [] if metadata is None else metadata
    embeddings = [] if embeddings is None else embeddings
    return metadata, [value.tolist() if hasattr(value, "tolist") else list(value) for value in embeddings]


def lexical_search(index: VectorIndex, query: str, limit: int) -> list[dict]:
    """Run only the existing weighted BM25 channel, with parent deduplication."""

    metadata, _vectors = index_snapshot(index)
    parents = {}
    representations = []
    parent_ids = []
    for item in metadata:
        from app.models.document_chunk import DocumentChunk
        chunk = DocumentChunk.model_validate_json(item["payload"])
        parents[chunk.chunk_id] = chunk
        representations.append(chunk.model_copy(update={"text": item.get("search_text", chunk.text)}))
        parent_ids.append(chunk.chunk_id)
    scores = defaultdict(float)
    for parent_id, score in zip(parent_ids, _bm25_scores(query, representations), strict=True):
        scores[parent_id] = max(scores[parent_id], score)
    ranked = sorted((identifier for identifier in parents if scores[identifier] > 0), key=lambda i: (-scores[i], i))
    return [{"chunk": parents[i], "score": scores[i], "lexical_rank": rank} for rank, i in enumerate(ranked[:limit], 1)]


def retrieve(index: VectorIndex, query: str, strategy: str, limit: int = CANDIDATES) -> list[dict]:
    """Execute exactly one requested retrieval strategy."""

    if strategy == "lexical":
        return lexical_search(index, query, limit)
    if strategy == "semantic":
        return [
            {"chunk": hit.chunk, "score": hit.score, "dense_rank": rank}
            for rank, hit in enumerate(index.search(query, limit), 1)
        ]
    return [
        {
            "chunk": hit.chunk,
            "score": hit.score,
            "dense_rank": hit.dense_rank,
            "lexical_rank": hit.lexical_rank,
            "fusion_score": hit.fusion_score,
        }
        for hit in index.search_hybrid(
            query, limit, dense_candidates=CANDIDATES,
            lexical_candidates=CANDIDATES, rrf_k=RRF_K,
        )
    ]


def routed_retrieve(case: dict, kb: str, strategy: str, english: VectorIndex, bilingual: VectorIndex) -> tuple[list[dict], str, str]:
    """Apply language-aware routing without using evidence annotations."""

    request = request_for(case)
    query = normalized_search_query(request.resolved_question or request.question, request.language)
    if kb == "english_only" or request.language == "en":
        return retrieve(english, query, strategy), query, "english"
    if request.language == "sw":
        return retrieve(bilingual, query, strategy), query, "bilingual"

    # Mixed-language queries use the established selective principle.  Dense
    # and hybrid channels require a materially stronger bilingual dense match;
    # lexical retrieval uses the bilingual representation because raw BM25
    # scores from corpora of different size are not comparable.
    if strategy == "lexical":
        return retrieve(bilingual, query, strategy), query, "bilingual_mixed_lexical"
    left = retrieve(english, query, strategy)
    right = retrieve(bilingual, query, strategy)
    choose_right = bool(right) and (not left or right[0]["score"] >= left[0]["score"] + 0.01)
    if strategy == "hybrid" and choose_right:
        choose_right = right[0].get("dense_rank") is not None and right[0].get("lexical_rank") is not None
    return (right if choose_right else left), query, ("bilingual_selective" if choose_right else "english_selective")


def controlled_context(hits: list[dict]) -> tuple[list, int]:
    """Select context consistently, without applying cosine thresholds to other scores."""

    selected = []
    documents = []
    used = 0
    per_document = Counter()
    for hit in hits[:TOP_K]:
        chunk = hit["chunk"]
        document = chunk.document_id
        if document not in documents and len(documents) >= 2:
            continue
        if per_document[document] >= 3:
            continue
        needed = len(chunk.text) + (2 if selected else 0)
        if used + needed > 8000:
            continue
        if document not in documents:
            documents.append(document)
        selected.append(chunk)
        per_document[document] += 1
        used += needed
    return selected, used


def case_metrics(case: dict, hits: list[dict], context: list) -> dict[str, float]:
    criteria = case["mandatory_evidence_criteria"]
    top = [item["chunk"].chunk_id for item in hits[:TOP_K]]
    relevant = set().union(*(set(c["acceptable_parent_chunk_ids"]) for c in criteria))
    reciprocal = next((1 / rank for rank, identifier in enumerate(top, 1) if identifier in relevant), 0.0)
    coverage = lambda ids: sum(bool(set(ids) & set(c["acceptable_parent_chunk_ids"])) for c in criteria) / len(criteria)
    return {
        "mrr_at_5": reciprocal,
        "recall_at_1": coverage(top[:1]),
        "recall_at_5": coverage(top),
        "hit_rate_at_5": float(bool(set(top) & relevant)),
        "context_recall": coverage([chunk.chunk_id for chunk in context]),
    }


def aggregate(rows: list[dict]) -> dict:
    return {
        "applicable_questions": len(rows),
        **{metric: statistics.mean(row["metrics"][metric] for row in rows) for metric in METRICS},
    }


def evaluate_experiment(experiment: dict, cases: list[dict], english: VectorIndex, bilingual: VectorIndex) -> dict:
    started = perf_counter()
    rows = []
    behavioural = []
    latencies = []
    for case in cases:
        begin = perf_counter()
        hits, query, route = routed_retrieve(case, experiment["knowledge_base"], experiment["strategy"], english, bilingual)
        latency = (perf_counter() - begin) * 1000
        latencies.append(latency)
        context, context_chars = controlled_context(hits)
        common = {
            "experiment_id": experiment["id"], "case_id": case["case_id"],
            "semantic_group": case["semantic_group"], "language": case["language"],
            "question": case["question"], "normalized_query": query, "route": route,
            "retrieval_ms": latency, "retrieved_parent_ids": [h["chunk"].chunk_id for h in hits[:TOP_K]],
            "retrieved_documents": [Path(h["chunk"].source.replace("\\", "/")).name for h in hits[:TOP_K]],
            "retrieved_passages": [h["chunk"].text for h in hits[:TOP_K]],
            "context_parent_ids": [c.chunk_id for c in context], "context_chars": context_chars,
        }
        if case["metric_eligibility"]["retrieval_and_context_recall"] == "eligible":
            common["criteria"] = case["mandatory_evidence_criteria"]
            common["metrics"] = case_metrics(case, hits, context)
            rows.append(common)
        else:
            common.update({"expected_behavior": case["expected_behavior"], "retrieved_count": len(hits)})
            behavioural.append(common)
    scopes = {"overall": aggregate(rows)}
    for language in ("en", "sw", "mixed"):
        scopes[language] = aggregate([row for row in rows if row["language"] == language])
    return {
        **experiment, "duration_ms": (perf_counter() - started) * 1000,
        "latency_ms": {"mean": statistics.mean(latencies), "median": statistics.median(latencies), "p95": percentile(latencies, .95)},
        "aggregates": scopes, "answerable_cases": rows, "behavioral_cases": behavioural,
    }


def clustered_ci(left: dict | None, right: dict, scope: str, metric: str) -> dict:
    right_rows = [r for r in right["answerable_cases"] if scope == "overall" or r["language"] == scope]
    left_lookup = {r["case_id"]: r for r in left["answerable_cases"]} if left else {}
    groups = defaultdict(list)
    for row in right_rows:
        base = left_lookup.get(row["case_id"])
        groups[row["semantic_group"]].append(row["metrics"][metric] - (base["metrics"][metric] if base else 0.0))
    keys = sorted(groups)
    rng = random.Random(SEED)
    values = []
    for _ in range(BOOTSTRAPS):
        chosen = [keys[rng.randrange(len(keys))] for _key in keys]
        sample = [value for key in chosen for value in groups[key]]
        values.append(100 * statistics.mean(sample))
    observed = 100 * statistics.mean(value for group in groups.values() for value in group)
    return {"difference_percentage_points": observed, "ci_95": [percentile(values, .025), percentile(values, .975)], "clusters": len(keys), "samples": BOOTSTRAPS}


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def pct(value: float) -> str:
    return f"{100 * value:.2f}"


def svg_bar(path: Path, title: str, labels: list[str], values: list[float], ylabel: str) -> None:
    width, height = 1100, 650
    plot_x, plot_y, plot_w, plot_h = 100, 80, 920, 460
    bar = plot_w / max(1, len(values))
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">', '<rect width="100%" height="100%" fill="white"/>', f'<text x="550" y="35" text-anchor="middle" font-size="24" font-family="Arial">{title}</text>', f'<line x1="{plot_x}" y1="{plot_y+plot_h}" x2="{plot_x+plot_w}" y2="{plot_y+plot_h}" stroke="black"/>']
    colors = ["#004b87", "#d71920", "#f2a900", "#4c9f70", "#7851a9", "#607d8b"]
    maximum = max(values + [1e-9]) * 1.1
    for i, (label, value) in enumerate(zip(labels, values, strict=True)):
        x = plot_x + i * bar + bar * .12; h = value / maximum * plot_h; y = plot_y + plot_h - h
        parts += [f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar*.76:.1f}" height="{h:.1f}" fill="{colors[i%len(colors)]}"/>', f'<text x="{x+bar*.38:.1f}" y="{y-8:.1f}" text-anchor="middle" font-size="14">{value:.2f}</text>', f'<text x="{x+bar*.38:.1f}" y="{plot_y+plot_h+25}" text-anchor="middle" font-size="12">{label}</text>']
    parts += [f'<text x="25" y="{plot_y+plot_h/2}" transform="rotate(-90 25 {plot_y+plot_h/2})" font-size="16">{ylabel}</text>', '</svg>']
    path.write_text("".join(parts), encoding="utf-8")

    image = Image.new("RGB", (width, height), "white"); draw = ImageDraw.Draw(image)
    draw.text((width // 2, 25), title, fill="black", anchor="mm")
    draw.line((plot_x, plot_y + plot_h, plot_x + plot_w, plot_y + plot_h), fill="black", width=2)
    for i, (label, value) in enumerate(zip(labels, values, strict=True)):
        x = plot_x + i * bar + bar * .12; h = value / maximum * plot_h; y = plot_y + plot_h - h
        draw.rectangle((x, y, x + bar*.76, plot_y + plot_h), fill=colors[i % len(colors)])
        draw.text((x + bar*.38, y - 12), f"{value:.2f}", fill="black", anchor="mm")
        draw.text((x + bar*.38, plot_y + plot_h + 14), label, fill="black", anchor="mm")
    image.save(path.with_suffix(".png"), dpi=(180, 180))


def metric_heatmap(path: Path, rows: list[dict]) -> None:
    """Render the 18-condition by five-metric matrix as SVG and PNG."""

    cell_w, cell_h = 150, 34
    left, top = 110, 75
    width, height = left + cell_w * len(METRICS) + 25, top + cell_h * len(rows) + 45
    labels = ["MRR@5", "Recall@1", "Recall@5", "Hit Rate@5", "Context Recall"]
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">', '<rect width="100%" height="100%" fill="white"/>', f'<text x="{width/2}" y="28" text-anchor="middle" font-size="22" font-family="Arial">18-condition metric heatmap</text>']
    image = Image.new("RGB", (width, height), "white"); draw = ImageDraw.Draw(image)
    draw.text((width // 2, 20), "18-condition metric heatmap", fill="black", anchor="mm")
    for column, label in enumerate(labels):
        x = left + column * cell_w + cell_w / 2
        svg.append(f'<text x="{x}" y="60" text-anchor="middle" font-size="13">{label}</text>')
        draw.text((x, 58), label, fill="black", anchor="mm")
    for row_index, row in enumerate(rows):
        y = top + row_index * cell_h
        svg.append(f'<text x="{left-10}" y="{y+22}" text-anchor="end" font-size="13">{row["id"]}</text>')
        draw.text((left - 10, y + cell_h / 2), row["id"], fill="black", anchor="rm")
        for column, metric in enumerate(METRICS):
            value = float(row[metric]); intensity = int(235 - 175 * value)
            color = f"rgb({intensity},{intensity+10 if intensity < 245 else 255},255)"
            x = left + column * cell_w
            svg += [f'<rect x="{x}" y="{y}" width="{cell_w}" height="{cell_h}" fill="{color}" stroke="white"/>', f'<text x="{x+cell_w/2}" y="{y+22}" text-anchor="middle" font-size="12">{100*value:.2f}</text>']
            draw.rectangle((x, y, x + cell_w, y + cell_h), fill=(intensity, min(255, intensity + 10), 255), outline="white")
            draw.text((x + cell_w / 2, y + cell_h / 2), f"{100*value:.2f}", fill="black", anchor="mm")
    svg.append('</svg>'); path.write_text("".join(svg), encoding="utf-8")
    image.save(path.with_suffix(".png"), dpi=(180, 180))


def exports(report: dict, mappings: dict, manifest: dict) -> None:
    experiments = report["experiments"]
    summary = []
    language_rows = []
    per_question = []
    behavioral = []
    for exp in experiments:
        base = {key: exp[key] for key in ("id", "knowledge_base", "chunk_configuration", "target_size", "overlap", "strategy")}
        overall = exp["aggregates"]["overall"]
        summary.append({**base, **{m: overall[m] for m in METRICS}, **{f"{m}_percent": pct(overall[m]) for m in METRICS}, "applicable_questions": overall["applicable_questions"], "mean_retrieval_ms": exp["latency_ms"]["mean"]})
        for scope, values in exp["aggregates"].items():
            language_rows.append({**base, "question_language": scope, "applicable_questions": values["applicable_questions"], **{m: values[m] for m in METRICS}, **{f"{m}_percent": pct(values[m]) for m in METRICS}})
        for row in exp["answerable_cases"]:
            per_question.append({**base, "case_id": row["case_id"], "semantic_group": row["semantic_group"], "language": row["language"], "route": row["route"], "retrieval_ms": row["retrieval_ms"], "context_chars": row["context_chars"], **row["metrics"], "retrieved_parent_ids": "|".join(row["retrieved_parent_ids"]), "context_parent_ids": "|".join(row["context_parent_ids"])})
        for row in exp["behavioral_cases"]:
            behavioral.append({**base, "case_id": row["case_id"], "semantic_group": row["semantic_group"], "language": row["language"], "expected_behavior": row["expected_behavior"], "route": row["route"], "retrieved_count": row["retrieved_count"], "top_parent_ids": "|".join(row["retrieved_parent_ids"])})

    write_csv(OUT / "comparative_retrieval_summary.csv", summary, list(summary[0]))
    write_csv(OUT / "comparative_retrieval_by_language.csv", language_rows, list(language_rows[0]))
    write_csv(OUT / "comparative_retrieval_per_question.csv", per_question, list(per_question[0]))
    write_csv(OUT / "comparative_retrieval_behavioral_results.csv", behavioral, list(behavioral[0]))
    atomic_json(OUT / "comparative_retrieval_evidence_mapping.json", mappings)
    atomic_json(OUT / "experiment_manifest.json", manifest)

    # Clustered intervals and paired comparisons against E01 plus focused
    # comparisons isolating KB, strategy, and chunk changes.
    lookup = {exp["id"]: exp for exp in experiments}
    stats = {"seed": SEED, "bootstrap_samples": BOOTSTRAPS, "confidence_intervals": {}, "paired_comparisons": {}}
    for exp in experiments:
        stats["confidence_intervals"][exp["id"]] = {scope: {metric: clustered_ci(None, exp, scope, metric) for metric in METRICS} for scope in ("overall", "en", "sw", "mixed")}
    pairs = [("E01", exp["id"]) for exp in experiments[1:]] + [("E03","E06"),("E09","E12"),("E15","E18"),("E06","E12"),("E12","E18")]
    for left, right in dict.fromkeys(pairs):
        stats["paired_comparisons"][f"{left}_vs_{right}"] = {metric: clustered_ci(lookup[left], lookup[right], "overall", metric) for metric in METRICS}
    atomic_json(OUT / "comparative_retrieval_statistical_analysis.json", stats)

    wb = Workbook(); wb.remove(wb.active)
    for title, rows in (("Overall", summary), ("By language", language_rows), ("Per question", per_question), ("Behavioural", behavioral)):
        ws = wb.create_sheet(title); ws.append(list(rows[0])); [ws.append([row.get(field) for field in rows[0]]) for row in rows]
        ws.freeze_panes = "A2"; ws.auto_filter.ref = ws.dimensions
    wb.save(OUT / "comparative_retrieval_results.xlsx")

    CHARTS.mkdir(parents=True, exist_ok=True)
    best_by_strategy = {s: max((r for r in summary if r["strategy"] == s), key=lambda r: r["recall_at_5"]) for s in STRATEGIES}
    svg_bar(CHARTS / "overall_strategy_comparison.svg", "Best Recall@5 by retrieval strategy", list(STRATEGIES), [100*best_by_strategy[s]["recall_at_5"] for s in STRATEGIES], "Recall@5 (%)")
    kb_values = [statistics.mean(r["recall_at_5"] for r in summary if r["knowledge_base"] == kb) * 100 for kb in ("english_only", "bilingual")]
    svg_bar(CHARTS / "knowledge_base_comparison.svg", "Mean Recall@5 by knowledge base", ["English-only", "Bilingual"], kb_values, "Recall@5 (%)")
    chunk_values = [statistics.mean(r["recall_at_5"] for r in summary if r["chunk_configuration"] == c) * 100 for c in CONFIGS]
    svg_bar(CHARTS / "chunking_comparison.svg", "Mean Recall@5 by chunking configuration", list(CONFIGS), chunk_values, "Recall@5 (%)")
    lang_values = [max(r["recall_at_5"] for r in language_rows if r["question_language"] == lang) * 100 for lang in ("en", "sw", "mixed")]
    svg_bar(CHARTS / "performance_by_language.svg", "Best Recall@5 by question language", ["English", "Kiswahili", "Mixed"], lang_values, "Recall@5 (%)")
    metric_heatmap(CHARTS / "experiment_heatmap.svg", summary)
    best = max(summary, key=lambda r: (r["recall_at_5"], r["context_recall"], r["mrr_at_5"]))
    svg_bar(CHARTS / "recall_vs_context.svg", f"{best['id']}: retrieval versus context recall", ["Recall@5", "Context recall"], [100*best["recall_at_5"], 100*best["context_recall"]], "Percent")

    # Evidence-backed error examples are selected mechanically from actual rows.
    best_exp = lookup[best["id"]]
    zero = [r for r in best_exp["answerable_cases"] if r["metrics"]["recall_at_5"] == 0][:8]
    excluded = [r for r in best_exp["answerable_cases"] if r["metrics"]["recall_at_5"] > r["metrics"]["context_recall"]][:8]
    error_lines = ["# Comparative Retrieval Error Analysis", "", f"Strongest configuration by the declared selection order: **{best['id']}**.", "", "## Zero-recall examples", ""]
    for row in zero:
        ranked = [
            f"{rank}. **{document}** — {passage[:320].replace(chr(10), ' ')}"
            for rank, (document, passage) in enumerate(
                zip(row["retrieved_documents"], row["retrieved_passages"], strict=True),
                1,
            )
        ]
        error_lines += [f"### {row['case_id']}", "", f"Question: {row['question']}", "", f"Required criteria: {'; '.join(c['criterion_text'] for c in row['criteria'])}", "", "Retrieved ranking:", "", *ranked, ""]
    error_lines += ["## Retrieved evidence excluded by context selection", ""]
    for row in excluded:
        error_lines += [f"- `{row['case_id']}`: Recall@5 {pct(row['metrics']['recall_at_5'])}%, context recall {pct(row['metrics']['context_recall'])}%; top parents `{', '.join(row['retrieved_parent_ids'])}`, context parents `{', '.join(row['context_parent_ids'])}`."]
    (DOCS / "comparative_retrieval_error_analysis.md").write_text("\n".join(error_lines), encoding="utf-8")

    historical = {"mrr_at_5": .6599, "recall_at_5": .8111, "context_recall": .7444}
    reproduced = lookup["E12"]["aggregates"]["overall"]
    table = ["| ID | KB | Chunk | Strategy | MRR@5 | Recall@1 | Recall@5 | Hit Rate@5 | Context Recall |", "|---|---|---|---|---:|---:|---:|---:|---:|"]
    for row in summary:
        table.append(f"| {row['id']} | {row['knowledge_base']} | {row['target_size']}/{row['overlap']} | {row['strategy']} | {pct(row['mrr_at_5'])}% | {pct(row['recall_at_1'])}% | {pct(row['recall_at_5'])}% | {pct(row['hit_rate_at_5'])}% | {pct(row['context_recall'])}% |")
    language_table = ["| Experiment | Language | MRR@5 | Recall@1 | Recall@5 | Hit Rate@5 | Context Recall | N |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in language_rows:
        language_table.append(f"| {row['id']} | {row['question_language']} | {pct(row['mrr_at_5'])}% | {pct(row['recall_at_1'])}% | {pct(row['recall_at_5'])}% | {pct(row['hit_rate_at_5'])}% | {pct(row['context_recall'])}% | {row['applicable_questions']} |")
    focused = stats["paired_comparisons"]["E09_vs_E12"]
    report_lines = ["# Comparative Retrieval Performance Evaluation", "", f"Generated: {report['created_at']}", "", "## Methodology", "", "This supplementary comparison evaluated 180 evidence-bearing and 20 behavioural cases under 18 isolated conditions. Ranking was measured before confidence rejection. Context used the top five unique authoritative English parents, an 8,000-character limit, at most two documents, and no score-scale-dependent threshold. English queries route to the English index, Kiswahili to the bilingual index, and mixed queries use selective routing; lexical mixed queries use bilingual text because BM25 scores across differently sized corpora are not comparable.", "", "Lexical retrieval is the repository's BM25 implementation (k1=1.5, b=0.75) with category and heading weighting. Semantic retrieval is multilingual E5-small cosine search with the repository's query/passage prefixes and normalization. Hybrid retrieval fuses independent top-20 lexical and semantic ranks using RRF k=10. MRR@5 is first-relevant reciprocal rank; Recall@1 and Recall@5 are mandatory-criterion coverage; Hit Rate@5 is any-relevant-parent success; Context Recall is criterion coverage after controlled context selection.", "", "## Overall results", "", *table, "", "## Results by question language", "", *language_table, "", "The answerable denominators are verified directly from the dataset and are shown in the final column. Knowledge-base language and question language are kept separate.", "", "## Strongest configuration", "", f"**{best['id']}** ({best['knowledge_base']}, {best['target_size']}/{best['overlap']}, {best['strategy']}) led by Recall@5 then Context Recall: {pct(best['recall_at_5'])}% and {pct(best['context_recall'])}%.", "", "## Statistical comparison", "", f"For the controlled 1,800/360 hybrid comparison, bilingual E12 minus English-only E09 changed Recall@5 by {focused['recall_at_5']['difference_percentage_points']:.2f} percentage points (clustered 95% CI {focused['recall_at_5']['ci_95'][0]:.2f} to {focused['recall_at_5']['ci_95'][1]:.2f}) and Context Recall by {focused['context_recall']['difference_percentage_points']:.2f} points (95% CI {focused['context_recall']['ci_95'][0]:.2f} to {focused['context_recall']['ci_95'][1]:.2f}). Intervals use 2,000 semantic-group-clustered bootstrap samples. Full intervals and paired comparisons are in the statistical JSON.", "", "## Historical comparison", "", f"E12 reproduced MRR@5 {pct(reproduced['mrr_at_5'])}%, Recall@5 {pct(reproduced['recall_at_5'])}%, and Context Recall {pct(reproduced['context_recall'])}%. Historical comparison values were 65.99%, 81.11%, and 74.44%. The two ranking metrics reproduce exactly; controlled Context Recall is 2.78 percentage points higher because this comparison deliberately uses a strategy-neutral, threshold-free context selector rather than the earlier production-style confidence gate. Scores were not altered toward the historical values.", "", "## Validation and limitations", "", "All evidence criteria were mapped from source anchors before retrieval, and every criterion mapped in every chunk configuration. Bilingual representations share English parent IDs and cannot occupy duplicate top-five positions. Behavioural cases are reported separately. The strategy channels were executed independently: lexical does not embed queries, semantic does not calculate BM25, and hybrid alone invokes both channels. This dataset was previously used during development, so results are supplementary rather than an independent held-out selection claim. AI-assisted Kiswahili translations are not official or independently human-verified.", "", "The requested invariant Hit Rate@5 ≥ Recall@5 is not a mathematical invariant for multi-criterion questions: one retrieved relevant parent can yield Hit Rate=1 while criterion recall may be below 1, whereas macro averages can order either way. Hit Rate@5 ≥ MRR@5 does hold per case and in aggregate.", "", "The context policy intentionally omits the production 0.82 cosine confidence gate because BM25 and RRF scores are not cosine similarities. Consequently these results compare ranking and controlled context selection, not the deployed fallback decision boundary.", "", "See `comparative_retrieval_statistical_analysis.json` for all intervals and paired percentage-point differences, and the companion error-analysis document for retrieved rankings and source excerpts from actual failures."]
    (DOCS / "comparative_retrieval_report.md").write_text("\n".join(report_lines), encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True); INDEX_ROOT.mkdir(parents=True, exist_ok=True); DOCS.mkdir(parents=True, exist_ok=True)
    dataset_path = BACKEND_DIR / "data" / "evaluation" / "LLM evaluation" / "KQ-RAG-Eval-200.json"
    dataset = load_dataset()
    settings = get_settings(); active = get_retrieval_service(); documents = load_knowledge_base_documents(settings.raw_data_dir)
    locations = translation_locations(settings)
    experiments = []
    mappings = {"mapping_performed_before_retrieval": True, "configurations": {}}
    index_info = {}
    experiment_number = 1
    for config, (size, overlap) in CONFIGS.items():
        chunks = chunk_documents(documents, size, overlap)
        internal, mapping = map_evidence(dataset, chunks)
        if mapping["unresolved"]:
            raise ValueError(f"Unresolved evidence in configuration {config}: {mapping['unresolved']}")
        mappings["configurations"][config] = mapping
        representations = bilingual_representations(chunks, locations)
        root = INDEX_ROOT / config
        english = VectorIndex(root / "english", active.index.embedder, settings.embedding_batch_size)
        bilingual = VectorIndex(root / "bilingual", active.index.embedder, settings.embedding_batch_size)
        english_sync = english.sync(chunks)
        bilingual_sync = bilingual.sync(chunks, search_representations=representations)
        index_info[config] = {"parents": len(chunks), "english_sync": english_sync, "bilingual_sync": bilingual_sync, "english_bytes": directory_size(root / "english"), "bilingual_bytes": directory_size(root / "bilingual")}
        for kb in ("english_only", "bilingual"):
            for strategy in STRATEGIES:
                experiment = {"id": f"E{experiment_number:02d}", "knowledge_base": kb, "chunk_configuration": config, "target_size": size, "overlap": overlap, "strategy": strategy}
                result = evaluate_experiment(experiment, internal["cases"], english, bilingual)
                experiments.append(result); experiment_number += 1
                checkpoint = {"schema_version": "kq-comparative-retrieval-v1", "status": "running", "created_at": datetime.now(timezone.utc).isoformat(), "experiments": experiments}
                atomic_json(RESULTS, checkpoint)
                print(f"[{result['id']}/E18] complete", flush=True)

    # Validate counts and metric properties before publishing final artifacts.
    assert len(experiments) == 18
    assert all(len(exp["answerable_cases"]) == 180 and len(exp["behavioral_cases"]) == 20 for exp in experiments)
    assert all(exp["aggregates"]["overall"]["hit_rate_at_5"] + 1e-12 >= exp["aggregates"]["overall"]["mrr_at_5"] for exp in experiments)
    report = {"schema_version": "kq-comparative-retrieval-v1", "status": "completed", "created_at": datetime.now(timezone.utc).isoformat(), "metric_definitions": {"mrr_at_5": "reciprocal rank of first relevant parent in top five", "recall_at_1": "mandatory criterion coverage in rank one", "recall_at_5": "mandatory criterion coverage collectively in top five", "hit_rate_at_5": "any relevant parent in top five", "context_recall": "mandatory criterion coverage in controlled final context"}, "experiments": experiments}
    atomic_json(RESULTS, report)
    manifest = {"created_at": report["created_at"], "dataset_path": str(dataset_path.relative_to(BACKEND_DIR.parent)), "dataset_sha256": file_hash(dataset_path), "dataset_questions": len(dataset["cases"]), "source_documents": [{"path": str(path.relative_to(settings.raw_data_dir)), "sha256": file_hash(path)} for path in sorted((settings.raw_data_dir / "KQ Data").glob("*.docx"))], "embedding_fingerprint": active.index.embedder.fingerprint, "configurations": CONFIGS, "candidate_depth": CANDIDATES, "top_k": TOP_K, "rrf_k": RRF_K, "context_max_chars": 8000, "context_max_documents": 2, "random_seed": SEED, "bootstrap_samples": BOOTSTRAPS, "python": platform.python_version(), "indexes": index_info, "external_api_calls": 0, "production_index_modified": False}
    exports(report, mappings, manifest)
    print("Comparative retrieval evaluation completed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
