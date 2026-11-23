"""Diagnose and optimize bilingual parent ranking and context selection.

This development-only experiment reuses the corrected 45-question dataset,
the active English index, and the isolated complete bilingual index. Evidence
annotations are used only for offline scoring and oracle diagnostics.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings
from app.models.retrieval import RankedChunk, RetrievalRequest
from app.rag.context import build_context as standard_build_context
from app.rag.retrieval import RetrievalService, get_retrieval_service
from app.rag.vector_index import VectorIndex
from scripts import experimental_evaluation
from scripts.advanced_multilingual_retrieval_experiment import (
    directory_size,
    summarize,
)
from scripts.compare_development_retrieval_optimizations import (
    corrected_dataset,
)
from scripts.experimental_evaluation import (
    aggregate_offline,
    build_request,
    evaluate_offline_case,
)


BILINGUAL_INDEX = (
    BACKEND_DIR
    / "data"
    / "evaluation"
    / "complete-bilingual-chroma"
)
OUTPUT = (
    BACKEND_DIR
    / "data"
    / "evaluation"
    / "bilingual-ranking-context-optimization.json"
)
ALIGNMENT_INPUT = (
    BACKEND_DIR
    / "data"
    / "evaluation"
    / "complete-bilingual-alignment.json"
)


def min_max(values: list[float]) -> list[float]:
    """Normalize one query's signals without mixing incompatible scales."""

    if not values:
        return []
    lower = min(values)
    span = max(values) - lower
    if span <= 1e-12:
        return [1.0 for _value in values]
    return [
        (value - lower) / span
        for value in values
    ]


class LanguageRouter:
    """Route using the application's detected request language."""

    def __init__(
        self,
        english: RetrievalService,
        bilingual: RetrievalService,
        bilingual_languages: set[str],
    ) -> None:
        self.english = english
        self.bilingual = bilingual
        self.bilingual_languages = bilingual_languages
        self.settings = bilingual.settings
        self.index = bilingual.index

    def retrieve(self, request: RetrievalRequest):
        service = (
            self.bilingual
            if request.language in self.bilingual_languages
            else self.english
        )
        return service.retrieve(request)


class SelectiveMixedRouter:
    """Use bilingual mixed retrieval only for a stronger consensus match."""

    def __init__(
        self,
        english: RetrievalService,
        bilingual: RetrievalService,
    ) -> None:
        self.english = english
        self.bilingual = bilingual
        self.settings = bilingual.settings
        self.index = bilingual.index

    def retrieve(self, request: RetrievalRequest):
        if request.language == "sw":
            return self.bilingual.retrieve(request)
        if request.language != "mixed":
            return self.english.retrieve(request)

        english = self.english.retrieve(request)
        bilingual = self.bilingual.retrieve(request)
        if not bilingual.results:
            return english
        if not english.results:
            return bilingual

        bilingual_top = bilingual.results[0]
        has_channel_consensus = (
            bilingual_top.dense_rank is not None
            and bilingual_top.lexical_rank is not None
        )
        materially_stronger_dense_match = (
            bilingual_top.score
            >= english.results[0].score + 0.01
        )
        if has_channel_consensus and materially_stronger_dense_match:
            return bilingual
        return english


class NormalizedParentReranker:
    """Rerank parents with normalized dense and lexical retrieval signals."""

    def __init__(self, retrieval) -> None:
        self.retrieval = retrieval
        self.settings = retrieval.settings
        self.index = retrieval.index

    def retrieve(self, request: RetrievalRequest):
        requested = request.top_k or self.settings.retrieval_top_k
        expanded_request = request.model_copy(
            update={"top_k": max(20, requested)},
        )
        response = self.retrieval.retrieve(expanded_request)
        results = response.results

        dense_similarity = min_max(
            [result.score for result in results],
        )
        dense_rank_signal = min_max(
            [
                1 / (result.dense_rank or 10_000)
                for result in results
            ],
        )
        lexical_rank_signal = min_max(
            [
                1 / (result.lexical_rank or 10_000)
                for result in results
            ],
        )

        scored = []
        for result, dense, dense_rank, lexical_rank in zip(
            results,
            dense_similarity,
            dense_rank_signal,
            lexical_rank_signal,
            strict=True,
        ):
            # Dense similarity leads because multilingual semantic matching is
            # the bilingual index's strongest signal. Rank-normalized lexical
            # evidence remains useful but cannot dominate on its raw scale.
            relevance = (
                0.55 * dense
                + 0.25 * dense_rank
                + 0.20 * lexical_rank
            )
            scored.append((relevance, result))

        ranked = sorted(
            scored,
            key=lambda item: (
                -item[0],
                -item[1].score,
                item[1].chunk.chunk_id,
            ),
        )[:requested]
        reranked = [
            RankedChunk(
                rank=rank,
                score=result.score,
                chunk=result.chunk,
                dense_rank=result.dense_rank,
                lexical_rank=result.lexical_rank,
                fusion_score=relevance,
            )
            for rank, (relevance, result) in enumerate(ranked, 1)
        ]
        return response.model_copy(
            update={
                "top_k": requested,
                "returned_count": len(reranked),
                "results": reranked,
            },
        )


def token_set(text: str) -> set[str]:
    """Return Unicode lexical terms for lightweight redundancy checks."""

    import re

    return set(
        re.findall(
            r"[^\W_]+",
            text.casefold(),
            flags=re.UNICODE,
        ),
    )


def overlap(left: str, right: str) -> float:
    """Calculate Jaccard overlap for parent-level MMR diversity."""

    left_terms = token_set(left)
    right_terms = token_set(right)
    union = left_terms | right_terms
    if not union:
        return 0.0
    return len(left_terms & right_terms) / len(union)


def aggressive_adaptive_build_context(retrieval, settings):
    """Retain the rejected, precision-oriented selector for comparison."""

    eligible = [
        result
        for result in retrieval.results
        if result.score >= settings.context_min_similarity
    ]
    if not eligible:
        return standard_build_context(retrieval, settings)

    dense_values = min_max([result.score for result in eligible])
    fusion_values = min_max(
        [result.fusion_score or 0.0 for result in eligible],
    )
    quality = {
        result.chunk.chunk_id: (
            0.65 * dense
            + 0.35 * fusion
        )
        for result, dense, fusion in zip(
            eligible,
            dense_values,
            fusion_values,
            strict=True,
        )
    }

    selected = []
    remaining = list(eligible)
    while remaining and len(selected) < 3:
        candidates = []
        for result in remaining:
            redundancy = max(
                (
                    overlap(
                        result.chunk.text,
                        chosen.chunk.text,
                    )
                    for chosen in selected
                ),
                default=0.0,
            )
            mmr = quality[result.chunk.chunk_id] - 0.20 * redundancy
            candidates.append((mmr, result))

        mmr, best = max(
            candidates,
            key=lambda item: (
                item[0],
                item[1].score,
                item[1].chunk.chunk_id,
            ),
        )
        # Always retain the strongest eligible parent. Additional evidence
        # must remain reasonably strong after the diversity penalty.
        if selected and mmr < 0.42:
            break
        selected.append(best)
        remaining.remove(best)

    selected_ids = {
        result.chunk.chunk_id
        for result in selected
    }
    filtered = [
        result.model_copy(update={"rank": rank})
        for rank, result in enumerate(
            (
                result
                for result in retrieval.results
                if result.chunk.chunk_id in selected_ids
            ),
            1,
        )
    ]
    prepared = standard_build_context(
        retrieval.model_copy(
            update={
                "returned_count": len(filtered),
                "results": filtered,
            },
        ),
        settings,
    )
    # Preserve the complete retrieval response for diagnostics and API parity.
    return prepared.model_copy(update={"retrieval": retrieval})


def diversity_adaptive_build_context(retrieval, settings):
    """Reorder eligible parents for diversity without a relative cutoff."""

    eligible = [
        result
        for result in retrieval.results
        if result.score >= settings.context_min_similarity
    ]
    if not eligible:
        return standard_build_context(retrieval, settings)

    dense_values = min_max([result.score for result in eligible])
    fusion_values = min_max(
        [result.fusion_score or 0.0 for result in eligible],
    )
    quality = {
        result.chunk.chunk_id: 0.70 * dense + 0.30 * fusion
        for result, dense, fusion in zip(
            eligible,
            dense_values,
            fusion_values,
            strict=True,
        )
    }

    ordered = []
    remaining = list(eligible)
    while remaining:
        best = max(
            remaining,
            key=lambda result: (
                quality[result.chunk.chunk_id]
                - 0.10
                * max(
                    (
                        overlap(
                            result.chunk.text,
                            chosen.chunk.text,
                        )
                        for chosen in ordered
                    ),
                    default=0.0,
                ),
                result.score,
                result.chunk.chunk_id,
            ),
        )
        ordered.append(best)
        remaining.remove(best)

    reranked = [
        result.model_copy(update={"rank": rank})
        for rank, result in enumerate(ordered, 1)
    ]
    prepared = standard_build_context(
        retrieval.model_copy(
            update={
                "returned_count": len(reranked),
                "results": reranked,
            },
        ),
        settings.model_copy(
            update={
                "context_max_documents": 3,
                "context_max_chunks_per_document": 2,
            },
        ),
    )
    return prepared.model_copy(update={"retrieval": retrieval})


def evaluate(
    dataset: dict,
    retrieval,
    settings,
    *,
    context_builder=None,
) -> dict:
    """Evaluate one retrieval/context configuration without provider calls."""

    original_builder = experimental_evaluation.build_context
    experimental_evaluation.build_context = (
        context_builder or standard_build_context
    )
    try:
        retrieval.index.embedder.embed_query("retrieval warm-up")
        started = perf_counter()
        cases = [
            evaluate_offline_case(
                case,
                retrieval,
                settings,
                5,
            )
            for case in dataset["cases"]
        ]
    finally:
        experimental_evaluation.build_context = original_builder

    return {
        "duration_ms": round((perf_counter() - started) * 1000, 3),
        "aggregates": aggregate_offline(cases),
        "cases": cases,
    }


def criterion_recall(
    identifiers: set[str],
    criteria: list[dict],
) -> float:
    """Calculate criterion recall for one offline diagnostic stage."""

    return sum(
        bool(
            identifiers
            & set(criterion["acceptable_parent_chunk_ids"])
        )
        for criterion in criteria
    ) / len(criteria)


def oracle_parent_ids(
    candidate_ids: list[str],
    criteria: list[dict],
    limit: int,
) -> set[str]:
    """Greedily maximize annotated criterion coverage for an offline oracle."""

    selected: set[str] = set()
    remaining = set(candidate_ids)
    covered: set[str] = set()

    while remaining and len(selected) < limit:
        best = None
        best_gain: set[str] = set()
        for identifier in remaining:
            gain = {
                criterion["criterion_id"]
                for criterion in criteria
                if criterion["criterion_id"] not in covered
                and identifier
                in criterion["acceptable_parent_chunk_ids"]
            }
            if len(gain) > len(best_gain):
                best = identifier
                best_gain = gain
        if best is None or not best_gain:
            break
        selected.add(best)
        remaining.remove(best)
        covered.update(best_gain)

    return selected


def stage_diagnostics(dataset: dict, retrieval, settings) -> dict:
    """Measure evidence loss from channel candidates through final context."""

    rows = []
    exclusion_reasons = Counter()
    for case in dataset["cases"]:
        criteria = case["mandatory_evidence_criteria"]
        if not criteria:
            continue

        request = build_request(case, 20)
        pool = retrieval.retrieve(request)
        dense = {
            result.chunk.chunk_id
            for result in pool.results
            if result.dense_rank is not None
            and result.dense_rank <= 20
        }
        lexical = {
            result.chunk.chunk_id
            for result in pool.results
            if result.lexical_rank is not None
            and result.lexical_rank <= 20
        }
        fused = {
            result.chunk.chunk_id
            for result in pool.results
        }

        top_five_response = retrieval.retrieve(
            request.model_copy(update={"top_k": 5}),
        )
        top_five = {
            result.chunk.chunk_id
            for result in top_five_response.results
        }
        threshold = {
            result.chunk.chunk_id
            for result in top_five_response.results
            if result.score >= settings.context_min_similarity
        }
        prepared = standard_build_context(top_five_response, settings)
        context = {
            source.chunk.chunk_id
            for source in prepared.sources
        }
        exclusion_reasons.update(
            excluded.reason
            for excluded in prepared.excluded
        )

        oracle_five = oracle_parent_ids(
            list(fused),
            criteria,
            5,
        )
        oracle_context = oracle_parent_ids(
            list(fused),
            criteria,
            3,
        )
        rows.append(
            {
                "case_id": case["case_id"],
                "language": case["language"],
                "dense_recall_at_20": criterion_recall(dense, criteria),
                "lexical_recall_at_20": criterion_recall(lexical, criteria),
                "fused_recall_at_20": criterion_recall(fused, criteria),
                "top_five_recall": criterion_recall(top_five, criteria),
                "post_threshold_recall": criterion_recall(
                    threshold,
                    criteria,
                ),
                "context_recall": criterion_recall(context, criteria),
                "oracle_top_five_recall": criterion_recall(
                    oracle_five,
                    criteria,
                ),
                "oracle_three_parent_context_recall": criterion_recall(
                    oracle_context,
                    criteria,
                ),
            },
        )

    stages = [
        "dense_recall_at_20",
        "lexical_recall_at_20",
        "fused_recall_at_20",
        "top_five_recall",
        "post_threshold_recall",
        "context_recall",
        "oracle_top_five_recall",
        "oracle_three_parent_context_recall",
    ]

    def means(selected: list[dict]) -> dict:
        return {
            stage: sum(row[stage] for row in selected) / len(selected)
            for stage in stages
        }

    return {
        "all": means(rows),
        "by_language": {
            language: means(
                [
                    row
                    for row in rows
                    if row["language"] == language
                ],
            )
            for language in ("en", "sw", "mixed")
        },
        "context_exclusion_reasons": dict(exclusion_reasons),
        "rows": rows,
        "notes": {
            "parent_deduplication": (
                "Representations are collapsed to authoritative parent IDs "
                "before channel ranking; coverage cannot decrease at this "
                "step, although parent rank can change."
            ),
            "confidence": (
                "The stored hit score is the maximum dense similarity over "
                "all English and Kiswahili segments for the parent."
            ),
        },
    }


def candidate_recall(dataset: dict, retrieval) -> dict:
    """Measure comparable candidate recall with one fixed top-20 method."""

    values = []
    for case in dataset["cases"]:
        criteria = case["mandatory_evidence_criteria"]
        if not criteria:
            continue
        # Use the same deterministic conversation-history resolver as the
        # top-five evaluation. Earlier experiments omitted this step, which
        # explains their lower and non-comparable candidate-pool figures.
        response = retrieval.retrieve(build_request(case, 20))
        recall = criterion_recall(
            {
                result.chunk.chunk_id
                for result in response.results
            },
            criteria,
        )
        values.append((case["language"], recall))

    return {
        "all": statistics.mean(value for _language, value in values),
        "by_language": {
            language: statistics.mean(
                value
                for row_language, value in values
                if row_language == language
            )
            for language in ("en", "sw", "mixed")
        },
        "zero_recall_cases": sum(value == 0 for _language, value in values),
    }


def segmentation_diagnostics(embedder) -> dict:
    """Prove that long bilingual representations are split without loss."""

    alignment = json.loads(
        ALIGNMENT_INPUT.read_text(encoding="utf-8"),
    )
    counts = []
    preservation_failures = []
    for parent in alignment["parents"]:
        for language, field in (
            ("en", "english_text"),
            ("sw", "kiswahili_search_text"),
        ):
            text = parent[field]
            segments = embedder.segments(text)
            counts.append(
                {
                    "parent_chunk_id": parent["parent_chunk_id"],
                    "language": language,
                    "segments": len(segments),
                },
            )
            if "".join(segments) != text:
                preservation_failures.append(
                    {
                        "parent_chunk_id": parent["parent_chunk_id"],
                        "language": language,
                    },
                )

    return {
        "representations_checked": len(counts),
        "split_representations": sum(
            item["segments"] > 1
            for item in counts
        ),
        "maximum_segments_per_representation": max(
            item["segments"]
            for item in counts
        ),
        "concatenation_preservation_failures": preservation_failures,
        "all_content_preserved": not preservation_failures,
    }


def main() -> int:
    """Run diagnostics, isolated variants, and a combined configuration."""

    settings = get_settings()
    dataset, corrections = corrected_dataset()
    english = get_retrieval_service()
    bilingual = RetrievalService(
        VectorIndex(
            BILINGUAL_INDEX,
            english.index.embedder,
            settings.embedding_batch_size,
        ),
        settings,
    )

    current_language_aware = LanguageRouter(
        english,
        bilingual,
        {"sw", "mixed"},
    )
    swahili_only_bilingual = LanguageRouter(
        english,
        bilingual,
        {"sw"},
    )
    selective_mixed = SelectiveMixedRouter(
        english,
        bilingual,
    )
    reranked_bilingual = NormalizedParentReranker(bilingual)
    reranked_language_aware = LanguageRouter(
        english,
        reranked_bilingual,
        {"sw", "mixed"},
    )
    reranked_swahili_only = LanguageRouter(
        english,
        reranked_bilingual,
        {"sw"},
    )

    configurations = {
        "english_only_baseline": (english, None),
        "current_language_aware_bilingual": (
            current_language_aware,
            None,
        ),
        "swahili_only_bilingual": (
            swahili_only_bilingual,
            None,
        ),
        "selective_mixed_bilingual": (
            selective_mixed,
            None,
        ),
        "normalized_parent_ranking": (
            reranked_language_aware,
            None,
        ),
        "aggressive_adaptive_context_rejected": (
            current_language_aware,
            aggressive_adaptive_build_context,
        ),
        "diversity_adaptive_context": (
            current_language_aware,
            diversity_adaptive_build_context,
        ),
        "combined_ranking_and_context": (
            reranked_language_aware,
            diversity_adaptive_build_context,
        ),
        "combined_swahili_only_routing": (
            reranked_swahili_only,
            diversity_adaptive_build_context,
        ),
    }

    variants = {}
    for name, (service, context_builder) in configurations.items():
        result = evaluate(
            dataset,
            service,
            settings,
            context_builder=context_builder,
        )
        result["summary"] = summarize(result)
        result["candidate_pool"] = candidate_recall(
            dataset,
            service,
        )
        variants[name] = result

    report = {
        "warning": (
            "Development partition only; no held-out data, external API, "
            "or generation provider was accessed."
        ),
        "configuration": {
            "questions": len(dataset["cases"]),
            "rrf_k": 10,
            "authoritative_parent_count": 188,
            "precision_at_5_theoretical_ceiling": 0.40,
            "active_index_overwritten": False,
        },
        "annotation_corrections": corrections,
        "stage_diagnostics": stage_diagnostics(
            dataset,
            current_language_aware,
            settings,
        ),
        "segmentation_diagnostics": segmentation_diagnostics(
            english.index.embedder,
        ),
        "variants": variants,
        "storage": {
            "active_english_bytes": directory_size(
                settings.chroma_persist_dir,
            ),
            "isolated_bilingual_bytes": directory_size(BILINGUAL_INDEX),
        },
    }
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                name: {
                    "aggregates": value["aggregates"],
                    "candidate_pool": value["candidate_pool"],
                    "summary": value["summary"],
                }
                for name, value in variants.items()
            },
            indent=2,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
