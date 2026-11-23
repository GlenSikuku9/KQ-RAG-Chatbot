"""Development-only contextual and parent-child retrieval experiments."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
from time import perf_counter


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings
from app.models.document_chunk import DocumentChunk
from app.rag.ingestion import write_json_atomic
from app.rag.retrieval import RetrievalService, get_retrieval_service, normalized_search_query
from app.rag.vector_index import VectorIndex
from scripts.compare_development_retrieval_optimizations import corrected_dataset
from scripts.experimental_evaluation import aggregate_offline, evaluate_offline_case
from scripts.experiment_section_aware_index import _visual_heading_locations
from docx import Document
from docx.oxml.ns import qn


OUTPUT = BACKEND_DIR / "data" / "evaluation" / "advanced-multilingual-retrieval-experiment.json"
CONTEXTUAL_INDEX = BACKEND_DIR / "data" / "evaluation" / "contextual-parent-chroma"
CHILD_INDEX = BACKEND_DIR / "data" / "evaluation" / "contextual-child-chroma"


def _block_number(location: str) -> int | None:
    match = re.search(r"Body/block (\d+)", location)
    return int(match.group(1)) if match else None


def source_headings(raw_dir: Path) -> dict[str, list[tuple[int, str]]]:
    result = {}
    for path in sorted(raw_dir.rglob("*.docx")):
        locations = _visual_heading_locations(path)
        document = Document(path)
        values = []
        for index, child in enumerate(document.element.body, 1):
            location = f"Body/block {index}"
            if location in locations and child.tag == qn("w:p"):
                text = " ".join("".join(child.itertext()).split())
                if text.casefold() not in {"note:", "learn more", "more information"}:
                    values.append((index, text))
        result[path.name] = values
    return result


def contextual_parents(raw_chunks: list[dict], headings: dict[str, list[tuple[int, str]]]):
    chunks = [DocumentChunk.model_validate(item) for item in raw_chunks]
    representations = {}
    heading_audit = {}
    contextual_chunks = []
    for chunk in chunks:
        name = Path(chunk.source.replace("\\", "/")).name
        positions = [number for location in chunk.source_locations if (number := _block_number(location))]
        start = min(positions) if positions else 0
        end = max(positions) if positions else start
        prior = [item for item in headings[name] if item[0] <= start]
        internal = [item for item in headings[name] if start < item[0] <= end]
        selected = (prior[-1:] + internal)[-3:]
        section_names = list(dict.fromkeys(text for _, text in selected))
        section = " / ".join(section_names) or None
        authoritative = chunk.model_copy(update={"section": section})
        contextual_chunks.append(authoritative)
        prefix = f"Document: {chunk.category}"
        if section:
            prefix += f"\nSection: {section}"
        representations[chunk.chunk_id] = [prefix + "\n\n" + chunk.text]
        heading_audit[chunk.chunk_id] = section_names
    return contextual_chunks, representations, heading_audit


def _child_texts(text: str, target: int = 650) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    output: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = paragraph if not current else current + "\n\n" + paragraph
        if current and len(candidate) > target:
            output.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        output.append(current)
    return output


def child_representations(chunks: list[DocumentChunk]) -> dict[str, list[str]]:
    result = {}
    for chunk in chunks:
        prefix = f"Document: {chunk.category}"
        if chunk.section:
            prefix += f"\nSection: {chunk.section}"
        result[chunk.chunk_id] = [prefix + "\n\n" + child for child in _child_texts(chunk.text)]
    return result


def evaluate(dataset: dict, retrieval: RetrievalService, settings) -> dict:
    retrieval.index.embedder.embed_query("advanced retrieval warm-up")
    started = perf_counter()
    cases = [evaluate_offline_case(case, retrieval, settings, 5) for case in dataset["cases"]]
    return {
        "duration_ms": round((perf_counter() - started) * 1000, 3),
        "aggregates": aggregate_offline(cases), "cases": cases,
    }


def diagnostics(dataset: dict, retrieval: RetrievalService) -> dict:
    rows = []
    for case in dataset["cases"]:
        criteria = case["mandatory_evidence_criteria"]
        if not criteria:
            continue
        query = normalized_search_query(case["question"], case["language"])
        hits = retrieval.index.search_hybrid(
            query, 20, dense_candidates=40, lexical_candidates=40, rrf_k=10,
        )
        ids = [hit.chunk.chunk_id for hit in hits]
        satisfied = sum(
            bool(set(criterion["acceptable_parent_chunk_ids"]) & set(ids))
            for criterion in criteria
        )
        rows.append({
            "case_id": case["case_id"], "language": case["language"],
            "candidate_criterion_recall_at_20": satisfied / len(criteria),
        })
    return {
        "mean_candidate_criterion_recall_at_20": sum(row["candidate_criterion_recall_at_20"] for row in rows) / len(rows),
        "by_language": {
            language: sum(row["candidate_criterion_recall_at_20"] for row in rows if row["language"] == language)
            / sum(row["language"] == language for row in rows)
            for language in ("en", "sw", "mixed")
        },
        "zero_recall_cases_at_20": sum(row["candidate_criterion_recall_at_20"] == 0 for row in rows),
        "cases": rows,
    }


def summarize(report: dict) -> dict:
    cases = report["cases"]
    applicable = [case for case in cases if case["retrieval"]["metrics"]["criterion_recall_at_k"]["applicable"]]
    return {
        "zero_recall_cases": sum(case["retrieval"]["metrics"]["criterion_recall_at_k"]["value"] == 0 for case in applicable),
        "mean_context_chars": sum(case["context"]["context_chars"] for case in applicable) / len(applicable),
        "retrieved_but_lost_in_context_cases": sum(
            case["retrieval"]["metrics"]["criterion_recall_at_k"]["value"]
            > case["context"]["metrics"]["context_recall"]["value"]
            for case in applicable
        ),
    }


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def main() -> int:
    settings = get_settings()
    dataset, corrections = corrected_dataset()
    processed = json.loads((settings.processed_data_dir / "kq_document_chunks.json").read_text(encoding="utf-8"))
    raw_chunks = processed["chunks"]
    headings = source_headings(settings.raw_data_dir / "KQ Data")
    parents, contextual, heading_audit = contextual_parents(raw_chunks, headings)
    base = get_retrieval_service()

    contextual_index = VectorIndex(CONTEXTUAL_INDEX, base.index.embedder, settings.embedding_batch_size)
    contextual_sync = contextual_index.sync(parents, search_representations=contextual)
    contextual_service = RetrievalService(contextual_index, settings)

    children = child_representations(parents)
    child_index = VectorIndex(CHILD_INDEX, base.index.embedder, settings.embedding_batch_size)
    child_sync = child_index.sync(parents, search_representations=children)
    child_service = RetrievalService(child_index, settings)

    variants = {
        "corrected_active_baseline": evaluate(dataset, base, settings),
        "contextual_parent": evaluate(dataset, contextual_service, settings),
        "contextual_parent_child": evaluate(dataset, child_service, settings),
        "active_context_threshold_0_8": evaluate(
            dataset,
            base,
            settings.model_copy(update={"context_min_similarity": 0.80}),
        ),
        "active_context_threshold_0_78": evaluate(
            dataset,
            base,
            settings.model_copy(update={"context_min_similarity": 0.78}),
        ),
    }
    for value in variants.values():
        value["summary"] = summarize(value)
    result = {
        "warning": "Development only; no held-out data or external APIs accessed.",
        "configuration": {"rrf_k": 10, "parent_count": len(parents)},
        "translation_audit": {
            "complete_kiswahili_policy_documents_found": 0,
            "decision": "not_executed",
            "reason": "The authoritative 14-document corpus is English-only and contains no verified corresponding Kiswahili translations. Creating translations would require an unapproved translator or unverifiable manual policy translation.",
        },
        "heading_recovery": {
            "visual_headings": sum(map(len, headings.values())),
            "parents_with_contextual_heading": sum(bool(value) for value in heading_audit.values()),
        },
        "annotation_corrections": corrections,
        "indexing": {
            "contextual_parent": contextual_sync,
            "contextual_parent_child": child_sync,
            "mean_children_per_parent": sum(map(len, children.values())) / len(children),
        },
        "variants": variants,
        "candidate_pool_diagnostics": {
            "baseline": diagnostics(dataset, base),
            "contextual_parent": diagnostics(dataset, contextual_service),
            "contextual_parent_child": diagnostics(dataset, child_service),
        },
        "storage_bytes": {
            "active": directory_size(settings.chroma_persist_dir),
            "contextual_parent": directory_size(CONTEXTUAL_INDEX),
            "contextual_parent_child": directory_size(CHILD_INDEX),
        },
    }
    write_json_atomic(OUTPUT, result)
    print(json.dumps({
        name: {"aggregates": value["aggregates"], "summary": value["summary"]}
        for name, value in variants.items()
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
