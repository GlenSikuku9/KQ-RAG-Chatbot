"""Compare general retrieval/context variants on development data only.

This script never passes evaluation answers or evidence labels into retrieval.
Annotations are used only after retrieval to score the recorded output.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from time import perf_counter
from typing import Callable


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import Settings
from app.rag.ingestion import write_json_atomic
from app.rag.retrieval import (
    RetrievalService,
    get_retrieval_service,
    normalized_search_query,
)
from scripts.experimental_evaluation import (
    DEFAULT_DEVELOPMENT_DATASET,
    aggregate_offline,
    evaluate_offline_case,
    validate_dataset,
)
from scripts.evaluation_annotation_corrections import corrected_parent_ids


OUTPUT = (
    BACKEND_DIR
    / "data"
    / "evaluation"
    / "development-retrieval-optimization-comparison.json"
)

@dataclass(frozen=True)
class Variant:
    name: str
    dense_candidates: int = 20
    lexical_candidates: int = 20
    rrf_k: int = 60
    use_expansion: bool = True
    dense_only: bool = False
    max_documents: int = 2
    max_chunks_per_document: int = 3


def corrected_dataset() -> tuple[dict, list[dict]]:
    dataset = json.loads(
        DEFAULT_DEVELOPMENT_DATASET.read_text(encoding="utf-8"),
    )
    validate_dataset(dataset, "development")
    dataset = deepcopy(dataset)
    changes = []

    for case in dataset["cases"]:
        for criterion in case["mandatory_evidence_criteria"]:
            before = criterion["acceptable_parent_chunk_ids"]
            after, actually_removed = corrected_parent_ids(
                criterion["criterion_id"],
                before,
            )
            if not after:
                raise ValueError(
                    f"Correction emptied {criterion['criterion_id']}.",
                )
            criterion["acceptable_parent_chunk_ids"] = after
            if actually_removed:
                changes.append({
                    "case_id": case["case_id"],
                    "criterion_id": criterion["criterion_id"],
                    "removed_parent_chunk_ids": actually_removed,
                })

    return dataset, changes


def variant_search(
    base: RetrievalService,
    variant: Variant,
) -> Callable:
    index = base.index
    dense_search = index.search
    hybrid_search = index.search_hybrid

    def search(request):
        natural_query = request.resolved_question or request.question
        lexical_query = (
            normalized_search_query(natural_query, request.language)
            if variant.use_expansion
            else natural_query
        )
        if variant.dense_only:
            return dense_search(natural_query, 5)
        return hybrid_search(
            lexical_query,
            5,
            dense_candidates=variant.dense_candidates,
            lexical_candidates=variant.lexical_candidates,
            rrf_k=variant.rrf_k,
        )

    return search


class VariantRetrieval(RetrievalService):
    def __init__(self, base: RetrievalService, variant: Variant):
        super().__init__(base.index, base.settings)
        self._search = variant_search(base, variant)
        self._variant = variant

    def retrieve(self, request):
        original_hybrid = self.index.search_hybrid
        original_dense = self.index.search
        try:
            if self._variant.dense_only:
                self.index.search = lambda _query, _limit: self._search(request)
                settings = self.settings.model_copy(
                    update={"hybrid_retrieval_enabled": False},
                )
            else:
                self.index.search_hybrid = (
                    lambda *_args, **_kwargs: self._search(request)
                )
                settings = self.settings.model_copy(
                    update={"hybrid_retrieval_enabled": True},
                )
            return RetrievalService(self.index, settings).retrieve(request)
        finally:
            self.index.search_hybrid = original_hybrid
            self.index.search = original_dense


def evaluate_variant(
    dataset: dict,
    base: RetrievalService,
    variant: Variant,
) -> dict:
    retrieval = VariantRetrieval(base, variant)
    context_settings: Settings = base.settings.model_copy(update={
        "context_max_documents": variant.max_documents,
        "context_max_chunks_per_document": (
            variant.max_chunks_per_document
        ),
    })
    cases = []
    started = perf_counter()
    for case in dataset["cases"]:
        cases.append(
            evaluate_offline_case(
                case,
                retrieval,
                context_settings,
                5,
            ),
        )
    return {
        "configuration": variant.__dict__,
        "duration_ms": round((perf_counter() - started) * 1000, 3),
        "aggregates": aggregate_offline(cases),
        "cases": cases,
    }


def main() -> int:
    dataset, corrections = corrected_dataset()
    base = get_retrieval_service()
    base.index.embedder.embed_query("retrieval comparison warm-up")
    variants = (
        Variant("corrected_current_baseline"),
        Variant("expanded_equal_rrf_k_10", rrf_k=10),
        Variant("expanded_equal_rrf_k_30", rrf_k=30),
        Variant("expanded_equal_rrf_k_100", rrf_k=100),
        Variant(
            "expanded_equal_candidates_40",
            dense_candidates=40,
            lexical_candidates=40,
        ),
        Variant(
            "expanded_equal_wider_context",
            max_documents=3,
            max_chunks_per_document=5,
        ),
        Variant("natural_query_equal_rrf", use_expansion=False),
        Variant("natural_query_dense_only", use_expansion=False, dense_only=True),
    )
    results = []
    for variant in variants:
        print(f"Evaluating {variant.name}...", flush=True)
        results.append(evaluate_variant(dataset, base, variant))
    write_json_atomic(OUTPUT, {
        "warning": "Development partition only; no held-out data accessed.",
        "annotation_corrections": corrections,
        "variants": results,
    })
    print(f"Saved comparison to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
