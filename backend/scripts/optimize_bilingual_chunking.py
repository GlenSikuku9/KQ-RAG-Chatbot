"""Controlled bilingual chunk-size and overlap optimization experiment.

The runner builds only isolated indexes. It remaps development evidence from
stable source locations and policy locators before retrieval, then compares
English-only and selective bilingual routing tracks with fixed retrieval and
context settings.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import random
import statistics
import sys
from time import perf_counter


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings
from app.models.document_chunk import DocumentChunk
from app.rag.chunking import chunk_documents
from app.rag.document_loader import (
    load_knowledge_base_document,
    load_knowledge_base_documents,
)
from app.rag.retrieval import RetrievalService, get_retrieval_service
from app.rag.vector_index import VectorIndex
from scripts.advanced_multilingual_retrieval_experiment import directory_size
from scripts.compare_development_retrieval_optimizations import corrected_dataset
from scripts.complete_bilingual_retrieval_experiment import (
    translated_block_map,
    translated_parent_text,
)
from scripts.experimental_evaluation import (
    aggregate_offline,
    build_request,
    evaluate_offline_case,
)
from scripts.optimize_bilingual_ranking_context import SelectiveMixedRouter


OUTPUT = (
    BACKEND_DIR
    / "data"
    / "evaluation"
    / "bilingual-chunking-optimization.json"
)
INDEX_ROOT = (
    BACKEND_DIR
    / "data"
    / "evaluation"
    / "bilingual-chunking-indexes"
)
TRANSLATION_DIR = (
    BACKEND_DIR
    / "data"
    / "translations"
    / "kiswahili"
)

OVERLAP_CONFIGURATIONS = {
    "A0_1600_250": (1600, 250),
    "A1_1600_0": (1600, 0),
    "A2_1600_320": (1600, 320),
    "A3_1600_400": (1600, 400),
    "A4_1600_480": (1600, 480),
}
SIZE_CONFIGURATIONS = {
    "B1_800": 800,
    "B2_1000": 1000,
    "B3_1200": 1200,
    "B4_1400": 1400,
    "B5_1600": 1600,
    "B6_1800": 1800,
    "B7_2000": 2000,
}


def percentile(values: list[float], fraction: float) -> float:
    """Return a deterministic nearest-rank percentile."""

    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, round((len(ordered) - 1) * fraction)),
    )
    return ordered[index]


def longest_suffix_prefix(left: str, right: str, maximum: int) -> int:
    """Measure exact realized text overlap between adjacent chunks."""

    ceiling = min(len(left), len(right), maximum * 3 + 2000)
    for length in range(ceiling, 0, -1):
        if left[-length:] == right[:length]:
            return length
    return 0


def token_jaccard(left: str, right: str) -> float:
    """Measure near duplication using Unicode lexical terms."""

    import re

    left_terms = set(re.findall(r"[^\W_]+", left.casefold()))
    right_terms = set(re.findall(r"[^\W_]+", right.casefold()))
    union = left_terms | right_terms
    return len(left_terms & right_terms) / len(union) if union else 0.0


def translated_locations(settings) -> dict[str, dict[str, str]]:
    """Load verified Kiswahili blocks keyed by original English filename."""

    result = {}
    for english_path in sorted(
        (settings.raw_data_dir / "KQ Data").glob("*.docx"),
    ):
        translation = (
            TRANSLATION_DIR
            / f"{english_path.stem} - SW.docx"
        )
        translated = load_knowledge_base_document(
            translation,
            TRANSLATION_DIR,
        )
        result[english_path.name] = translated_block_map(translated)
    return result


def bilingual_representations(
    chunks: list[DocumentChunk],
    locations: dict[str, dict[str, str]],
) -> dict[str, list[str]]:
    """Align unchanged translations to each experimental English parent."""

    representations = {}
    for chunk in chunks:
        source_name = Path(chunk.source.replace("\\", "/")).name
        representations[chunk.chunk_id] = [
            chunk.text,
            translated_parent_text(
                chunk,
                locations[source_name],
            ),
        ]
    return representations


def remap_dataset(
    dataset: dict,
    old_chunks: list[dict],
    new_chunks: list[DocumentChunk],
) -> tuple[dict, dict]:
    """Remap evidence before retrieval using locators and source locations."""

    old = {
        chunk["chunk_id"]: chunk
        for chunk in old_chunks
    }
    new_by_source = defaultdict(list)
    for chunk in new_chunks:
        source_name = Path(chunk.source.replace("\\", "/")).name
        new_by_source[source_name].append(chunk)

    remapped = deepcopy(dataset)
    new_parent_ids = {
        chunk.chunk_id
        for chunk in new_chunks
    }
    audit = []
    for case in remapped["cases"]:
        for criterion in case["mandatory_evidence_criteria"]:
            original_ids = set(
                criterion["acceptable_parent_chunk_ids"]
            )

            # A configuration that reproduces the authoritative parent IDs
            # needs no semantic remapping. Keeping the reviewed IDs verbatim
            # prevents a broad locator from expanding baseline relevance.
            if original_ids <= new_parent_ids:
                audit.append(
                    {
                        "case_id": case["case_id"],
                        "criterion_id": criterion["criterion_id"],
                        "method": "unchanged_parent_ids",
                        "old_parent_count": len(original_ids),
                        "new_parent_count": len(original_ids),
                    },
                )
                continue

            originals = [
                old[identifier]
                for identifier in original_ids
            ]
            sources = {
                Path(item["source"].replace("\\", "/")).name
                for item in originals
            }
            locator = " ".join(
                criterion.get("policy_text_locator", "").split()
            ).casefold()
            candidates = [
                chunk
                for source in sources
                for chunk in new_by_source[source]
            ]
            locator_matches = [
                chunk
                for chunk in candidates
                if locator
                and locator
                in " ".join(chunk.text.split()).casefold()
            ]

            method = "policy_text_locator"
            mapped = locator_matches
            if not mapped:
                method = "source_location_intersection"
                locations = {
                    location
                    for item in originals
                    for location in item.get("source_locations", [])
                }
                mapped = [
                    chunk
                    for chunk in candidates
                    if locations & set(chunk.source_locations)
                ]
            if not mapped:
                raise ValueError(
                    f"Could not remap {criterion['criterion_id']}.",
                )

            criterion["acceptable_parent_chunk_ids"] = sorted(
                chunk.chunk_id
                for chunk in mapped
            )
            audit.append(
                {
                    "case_id": case["case_id"],
                    "criterion_id": criterion["criterion_id"],
                    "method": method,
                    "old_parent_count": len(originals),
                    "new_parent_count": len(mapped),
                },
            )

    return remapped, {
        "criteria": audit,
        "locator_mapped": sum(
            item["method"] == "policy_text_locator"
            for item in audit
        ),
        "location_fallback_mapped": sum(
            item["method"] == "source_location_intersection"
            for item in audit
        ),
        "unchanged_parent_ids": sum(
            item["method"] == "unchanged_parent_ids"
            for item in audit
        ),
    }


def chunk_statistics(
    chunks: list[DocumentChunk],
    representations: dict[str, list[str]],
    embedder,
    configured_overlap: int,
) -> dict:
    """Measure chunk shape, actual overlap, token use, and preservation."""

    model = embedder._get_model()
    token_counts = []
    segmented = 0
    preservation_failures = 0
    representation_count = 0
    for values in representations.values():
        for text in values:
            representation_count += 1
            tokens = model.tokenizer(
                "passage: " + text,
                add_special_tokens=True,
                truncation=False,
            )["input_ids"]
            token_counts.append(len(tokens))
            parts = embedder.segments(text)
            segmented += len(parts) > 1
            preservation_failures += "".join(parts) != text

    by_document = defaultdict(list)
    for chunk in chunks:
        by_document[chunk.document_id].append(chunk)
    overlaps = []
    near_duplicate_pairs = 0
    adjacent_pairs = 0
    for document_chunks in by_document.values():
        ordered = sorted(document_chunks, key=lambda item: item.chunk_index)
        for left, right in zip(ordered, ordered[1:]):
            adjacent_pairs += 1
            overlaps.append(
                longest_suffix_prefix(
                    left.text,
                    right.text,
                    configured_overlap,
                ),
            )
            near_duplicate_pairs += token_jaccard(
                left.text,
                right.text,
            ) >= 0.90

    lengths = [len(chunk.text) for chunk in chunks]
    covered_locations = {
        (chunk.document_id, location)
        for chunk in chunks
        for location in chunk.source_locations
    }
    return {
        "english_parent_chunks": len(chunks),
        "kiswahili_representations": len(chunks),
        "total_bilingual_representations": representation_count,
        "characters": {
            "minimum": min(lengths),
            "maximum": max(lengths),
            "mean": statistics.mean(lengths),
            "median": statistics.median(lengths),
            "p95": percentile(lengths, 0.95),
        },
        "embedding_tokens_with_prefix": {
            "minimum": min(token_counts),
            "maximum_unsplit": max(token_counts),
            "mean": statistics.mean(token_counts),
            "median": statistics.median(token_counts),
            "p95": percentile(token_counts, 0.95),
        },
        "actual_adjacent_overlap_characters": {
            "minimum": min(overlaps, default=0),
            "maximum": max(overlaps, default=0),
            "mean": statistics.mean(overlaps) if overlaps else 0.0,
            "median": statistics.median(overlaps) if overlaps else 0.0,
            "nonzero_rate": (
                sum(value > 0 for value in overlaps) / len(overlaps)
                if overlaps
                else 0.0
            ),
        },
        "adjacent_pairs": adjacent_pairs,
        "near_duplicate_adjacent_pairs": near_duplicate_pairs,
        "near_duplicate_adjacent_rate": (
            near_duplicate_pairs / adjacent_pairs
            if adjacent_pairs
            else 0.0
        ),
        "representations_requiring_segmentation": segmented,
        "content_preservation_failures": preservation_failures,
        "content_preservation_rate": (
            1 - preservation_failures / representation_count
        ),
        "unique_source_locations_covered": len(covered_locations),
    }


def evaluate(dataset: dict, retrieval, settings) -> dict:
    """Run the established top-five evaluation and extra recall diagnostics."""

    started = perf_counter()
    cases = [
        evaluate_offline_case(case, retrieval, settings, 5)
        for case in dataset["cases"]
    ]
    result = {
        "duration_ms": round((perf_counter() - started) * 1000, 3),
        "aggregates": aggregate_offline(cases),
        "cases": cases,
    }

    recalls = {depth: [] for depth in (1, 3, 10, 20)}
    latency = []
    duplicate_rates = []
    unique_evidence = []
    for case in dataset["cases"]:
        criteria = case["mandatory_evidence_criteria"]
        if not criteria:
            continue
        response = retrieval.retrieve(build_request(case, 20))
        latency.append(response.retrieval_ms)
        ids = [result.chunk.chunk_id for result in response.results]
        for depth in recalls:
            selected = set(ids[:depth])
            recalls[depth].append(
                sum(
                    bool(
                        selected
                        & set(criterion["acceptable_parent_chunk_ids"])
                    )
                    for criterion in criteria
                )
                / len(criteria),
            )

        top_five = response.results[:5]
        duplicate_pairs = 0
        pairs = 0
        for index, left in enumerate(top_five):
            for right in top_five[index + 1 :]:
                pairs += 1
                duplicate_pairs += (
                    left.chunk.document_id == right.chunk.document_id
                    and token_jaccard(
                        left.chunk.text,
                        right.chunk.text,
                    ) >= 0.90
                )
        duplicate_rates.append(duplicate_pairs / pairs if pairs else 0.0)
        unique_evidence.append(
            len({result.chunk.chunk_id for result in top_five}),
        )

    result["diagnostics"] = {
        "criterion_recall": {
            f"at_{depth}": statistics.mean(values)
            for depth, values in recalls.items()
        },
        "candidate_to_top5_loss": (
            statistics.mean(recalls[20])
            - result["aggregates"]["all"]["criterion_recall_at_5"]["value"]
        ),
        "retrieval_latency_ms": {
            "mean": statistics.mean(latency),
            "median": statistics.median(latency),
            "p95": percentile(latency, 0.95),
        },
        "mean_top5_near_duplicate_pair_rate": statistics.mean(
            duplicate_rates,
        ),
        "mean_unique_top5_parent_count": statistics.mean(unique_evidence),
        "mean_context_tokens": statistics.mean(
            case["context"]["context_tokens_estimate"]
            for case in cases
            if case["metric_eligibility"]["retrieval_and_context_recall"]
            == "eligible"
        ),
        "zero_recall_at_5": sum(
            case["retrieval"]["metrics"]["criterion_recall_at_k"]["value"]
            == 0
            for case in cases
            if case["retrieval"]["metrics"]["criterion_recall_at_k"]["applicable"]
        ),
    }
    return result


def bootstrap_delta(
    baseline: dict,
    candidate: dict,
    path: tuple[str, ...],
    samples: int = 2000,
) -> dict:
    """Return a paired development-case bootstrap interval in percentage points."""

    pairs = []
    for left, right in zip(
        baseline["cases"],
        candidate["cases"],
        strict=True,
    ):
        left_value = left
        right_value = right
        for key in path:
            left_value = left_value[key]
            right_value = right_value[key]
        if left_value["applicable"] and right_value["applicable"]:
            pairs.append((left_value["value"], right_value["value"]))

    randomizer = random.Random(20261118)
    deltas = []
    for _sample in range(samples):
        selected = [
            pairs[randomizer.randrange(len(pairs))]
            for _pair in pairs
        ]
        deltas.append(
            100
            * statistics.mean(right - left for left, right in selected),
        )
    return {
        "delta_percentage_points": 100
        * statistics.mean(right - left for left, right in pairs),
        "bootstrap_95_percent_interval": [
            percentile(deltas, 0.025),
            percentile(deltas, 0.975),
        ],
        "paired_cases": len(pairs),
        "samples": samples,
    }


def build_configuration(
    name: str,
    size: int,
    overlap: int,
    documents,
    locations,
    dataset,
    old_chunks,
    active,
    settings,
) -> dict:
    """Build isolated English/bilingual indexes and evaluate both tracks."""

    chunks = chunk_documents(documents, size, overlap)
    representations = bilingual_representations(chunks, locations)
    remapped, remap_audit = remap_dataset(dataset, old_chunks, chunks)
    configuration_root = INDEX_ROOT / name
    english_path = configuration_root / "english"
    bilingual_path = configuration_root / "bilingual"

    english_index = VectorIndex(
        english_path,
        active.index.embedder,
        settings.embedding_batch_size,
    )
    bilingual_index = VectorIndex(
        bilingual_path,
        active.index.embedder,
        settings.embedding_batch_size,
    )
    started = perf_counter()
    english_sync = english_index.sync(chunks)
    bilingual_sync = bilingual_index.sync(
        chunks,
        search_representations=representations,
    )
    indexing_ms = (perf_counter() - started) * 1000

    experimental_settings = settings.model_copy(
        update={"hybrid_rrf_k": 10},
    )
    english = RetrievalService(english_index, experimental_settings)
    bilingual = RetrievalService(bilingual_index, experimental_settings)
    selective = SelectiveMixedRouter(english, bilingual)

    return {
        "configuration": {
            "target_chunk_size": size,
            "configured_overlap": overlap,
            "configured_overlap_fraction": overlap / size,
        },
        "evidence_remapping": remap_audit,
        "chunk_quality": chunk_statistics(
            chunks,
            representations,
            active.index.embedder,
            overlap,
        ),
        "indexing": {
            "duration_ms": indexing_ms,
            "english_sync": english_sync,
            "bilingual_sync": bilingual_sync,
            "english_storage_bytes": directory_size(english_path),
            "bilingual_storage_bytes": directory_size(bilingual_path),
            "total_storage_bytes": directory_size(configuration_root),
        },
        "tracks": {
            "production_style_english_only": evaluate(
                remapped,
                english,
                experimental_settings,
            ),
            "selective_bilingual": evaluate(
                remapped,
                selective,
                experimental_settings,
            ),
        },
    }


def selection_score(result: dict) -> tuple:
    """Prioritize recall without accepting large precision or MRR regressions."""

    metrics = result["tracks"]["selective_bilingual"]["aggregates"]["all"]
    return (
        metrics["criterion_recall_at_5"]["value"],
        metrics["context_recall"]["value"],
        metrics["mrr_at_5"]["value"],
        metrics["context_precision"]["value"],
    )


def main() -> int:
    """Run overlap first, then size only when overlap evidence justifies it."""

    settings = get_settings()
    dataset, corrections = corrected_dataset()
    active = get_retrieval_service()
    documents = load_knowledge_base_documents(
        # Load from the configured corpus root so each document retains the
        # same source-relative path used by production ingestion. Starting at
        # the nested KQ Data directory changes parent hashes despite identical
        # policy text and would invalidate baseline reproduction.
        settings.raw_data_dir,
    )
    locations = translated_locations(settings)
    processed = json.loads(
        (
            settings.processed_data_dir
            / "kq_document_chunks.json"
        ).read_text(encoding="utf-8"),
    )
    old_chunks = processed["chunks"]

    overlap_results = {}
    for name, (size, overlap) in OVERLAP_CONFIGURATIONS.items():
        overlap_results[name] = build_configuration(
            name,
            size,
            overlap,
            documents,
            locations,
            dataset,
            old_chunks,
            active,
            settings,
        )

    baseline = overlap_results["A0_1600_250"]
    best_overlap_name = max(
        overlap_results,
        key=lambda name: selection_score(overlap_results[name]),
    )
    best_overlap = overlap_results[best_overlap_name]
    base_metrics = baseline["tracks"]["selective_bilingual"]["aggregates"]["all"]
    best_metrics = best_overlap["tracks"]["selective_bilingual"]["aggregates"]["all"]
    improvement = max(
        best_metrics["criterion_recall_at_5"]["value"]
        - base_metrics["criterion_recall_at_5"]["value"],
        best_metrics["context_recall"]["value"]
        - base_metrics["context_recall"]["value"],
    )

    # A one-point recall change or a non-baseline best result justifies the
    # prespecified size sweep. Otherwise size search would add cost without
    # diagnostic evidence that boundaries matter.
    size_experiment_justified = (
        improvement >= 0.01
        or best_overlap_name != "A0_1600_250"
    )
    size_results = {}
    interaction_results = {}
    selected_overlap_fraction = (
        best_overlap["configuration"]["configured_overlap_fraction"]
    )
    if size_experiment_justified:
        for name, size in SIZE_CONFIGURATIONS.items():
            overlap = round(size * selected_overlap_fraction)
            size_results[name] = build_configuration(
                f"{name}_{overlap}",
                size,
                overlap,
                documents,
                locations,
                dataset,
                old_chunks,
                active,
                settings,
            )

        best_size_name = max(
            size_results,
            key=lambda name: selection_score(size_results[name]),
        )
        best_size = size_results[best_size_name]
        size_metrics = best_size["tracks"]["selective_bilingual"]["aggregates"]["all"]
        substantial_size_gain = max(
            size_metrics["criterion_recall_at_5"]["value"]
            - best_metrics["criterion_recall_at_5"]["value"],
            size_metrics["context_recall"]["value"]
            - best_metrics["context_recall"]["value"],
        ) >= 0.02
        if substantial_size_gain:
            size = best_size["configuration"]["target_chunk_size"]
            for fraction in (0.15625, 0.20, 0.25, 0.30):
                overlap = round(size * fraction)
                name = f"I_{size}_{overlap}"
                interaction_results[name] = build_configuration(
                    name,
                    size,
                    overlap,
                    documents,
                    locations,
                    dataset,
                    old_chunks,
                    active,
                    settings,
                )

    comparisons = {}
    for name, result in {
        **overlap_results,
        **size_results,
        **interaction_results,
    }.items():
        comparisons[name] = {
            "criterion_recall_at_5": bootstrap_delta(
                baseline["tracks"]["selective_bilingual"],
                result["tracks"]["selective_bilingual"],
                (
                    "retrieval",
                    "metrics",
                    "criterion_recall_at_k",
                ),
            ),
            "context_recall": bootstrap_delta(
                baseline["tracks"]["selective_bilingual"],
                result["tracks"]["selective_bilingual"],
                ("context", "metrics", "context_recall"),
            ),
        }

    report = {
        "warning": (
            "Development-only isolated experiment; no held-out data or "
            "production index was accessed for writing."
        ),
        "selection_criteria_declared_before_results": [
            "maximize Criterion Recall@5",
            "then Context Recall",
            "then MRR@5",
            "then Context Precision",
            "reject material multilingual or efficiency regressions",
        ],
        "verified_baseline": {
            "chunk_size": settings.rag_chunk_size,
            "chunk_overlap": settings.rag_chunk_overlap,
            "overlap_fraction": (
                settings.rag_chunk_overlap / settings.rag_chunk_size
            ),
            "processed_parent_count": len(old_chunks),
            "development_questions": len(dataset["cases"]),
            "language_distribution": dict(
                Counter(case["language"] for case in dataset["cases"]),
            ),
        },
        "annotation_corrections": corrections,
        "overlap_experiment": overlap_results,
        "overlap_selection": {
            "best_configuration": best_overlap_name,
            "size_experiment_justified": size_experiment_justified,
            "maximum_recall_improvement": improvement,
        },
        "size_experiment": size_results,
        "interaction_experiment": interaction_results,
        "paired_bootstrap_comparisons": comparisons,
        "active_index_overwritten": False,
    }
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "best_overlap": best_overlap_name,
                "size_experiment_justified": size_experiment_justified,
                "overlap_metrics": {
                    name: result["tracks"]["selective_bilingual"]["aggregates"]["all"]
                    for name, result in overlap_results.items()
                },
                "size_configurations_run": list(size_results),
                "interaction_configurations_run": list(
                    interaction_results,
                ),
            },
            indent=2,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
