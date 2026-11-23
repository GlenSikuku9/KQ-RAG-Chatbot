"""Compare dense and hybrid retrieval against manually labelled excerpts.

This development tool uses the local processed corpus and active Chroma index.
It never calls a generation model or writes to Firestore.
"""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sys
from time import perf_counter
import tracemalloc

from pydantic import TypeAdapter


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(BACKEND_DIR))

from app.config import get_settings
from app.models.document_chunk import DocumentChunk
from app.models.retrieval import RetrievalRequest
from app.rag.context import build_context
from app.rag.ingestion import write_json_atomic
from app.rag.retrieval import RetrievalService, get_retrieval_service


DEFAULT_CORPUS = (
    BACKEND_DIR
    / "data"
    / "processed"
    / "kq_document_chunks.json"
)
DEFAULT_QUESTIONS = (
    BACKEND_DIR
    / "data"
    / "questions"
    / "retrieval_questions.json"
)


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _load_chunks(path: Path) -> list[DocumentChunk]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return TypeAdapter(list[DocumentChunk]).validate_python(
        payload["chunks"],
    )


def _relevant_chunk_ids(
    case: dict,
    chunks: list[DocumentChunk],
) -> set[str]:
    if case["source"] is None:
        return set()

    relevant = {
        chunk.chunk_id
        for chunk in chunks
        if Path(chunk.source).name == case["source"]
        and all(
            _normalized(excerpt) in _normalized(chunk.text)
            for excerpt in case["evidence"]
        )
    }
    if not relevant:
        raise ValueError(
            f"Labelled evidence is absent for {case['id']}.",
        )
    return relevant


def _first_rank(results, relevant: set[str]) -> int | None:
    return next(
        (
            rank
            for rank, result in enumerate(results, 1)
            if result.chunk.chunk_id in relevant
        ),
        None,
    )


def _candidate(result) -> dict:
    return {
        "rank": result.rank,
        "document": Path(result.chunk.source).name,
        "chunk_id": result.chunk.chunk_id,
        "chunk_index": result.chunk.chunk_index,
        "cosine_similarity": result.score,
        "dense_rank": result.dense_rank,
        "lexical_rank": result.lexical_rank,
        "fusion_score": result.fusion_score,
        # A bounded preview keeps reports inspectable without duplicating the
        # entire policy corpus. Chunk IDs point back to the complete evidence.
        "text_preview": _normalized(result.chunk.text)[:500],
    }


def _aggregate(outcomes: list[dict], method: str) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for outcome in outcomes:
        if outcome["answerable"]:
            groups["all"].append(outcome)
            groups[outcome["language"]].append(outcome)

    metrics = {}
    for language, items in groups.items():
        metrics[language] = {
            "questions": len(items),
            "precision_at_k": sum(
                item[method]["precision_at_k"]
                for item in items
            ) / len(items),
            "recall_at_k": sum(
                item[method]["recall_at_k"]
                for item in items
            ) / len(items),
            "mrr_at_k": sum(
                item[method]["reciprocal_rank_at_k"]
                for item in items
            ) / len(items),
            "mean_retrieval_ms": sum(
                item[method]["retrieval_ms"]
                for item in items
            ) / len(items),
            "mean_context_ms": sum(
                item[method]["context_ms"]
                for item in items
            ) / len(items),
        }
    return metrics


def compare(
    chunks: list[DocumentChunk],
    cases: list[dict],
    top_k: int,
) -> dict:
    settings = get_settings()
    index = get_retrieval_service().index
    services = {
        "dense": RetrievalService(
            index,
            settings.model_copy(
                update={"hybrid_retrieval_enabled": False},
            ),
        ),
        "hybrid": RetrievalService(
            index,
            settings.model_copy(
                update={"hybrid_retrieval_enabled": True},
            ),
        ),
    }

    # Warm the shared local model before measuring steady-state retrieval.
    index.embedder.embed_query("retrieval audit warm-up")
    outcomes = []
    tracemalloc.start()

    for case in cases:
        relevant = _relevant_chunk_ids(case, chunks)
        outcome = {
            "id": case["id"],
            "language": case["language"],
            "question": case["question"],
            "answerable": bool(relevant),
            "labelled_relevant_chunk_ids": sorted(relevant),
        }

        for method, service in services.items():
            response = service.retrieve(
                RetrievalRequest(
                    question=case["question"],
                    language=case["language"],
                    top_k=top_k,
                ),
            )
            context_started = perf_counter()
            prepared = build_context(response, service.settings)
            context_ms = (
                perf_counter() - context_started
            ) * 1000
            retrieved_ids = {
                result.chunk.chunk_id
                for result in response.results
            }
            selected_ids = {
                source.chunk.chunk_id
                for source in prepared.sources
            }
            first_rank = _first_rank(response.results, relevant)
            matched_retrieval = relevant & retrieved_ids
            matched_context = relevant & selected_ids
            expected_context = bool(relevant)
            actual_context = prepared.decision == "context_available"

            outcome[method] = {
                "ranking_method": response.ranking_method,
                "retrieval_ms": response.retrieval_ms,
                "context_ms": round(context_ms, 3),
                "context_chars": prepared.context_chars,
                "context_tokens": prepared.context_tokens,
                "first_relevant_rank": first_rank,
                "precision_at_k": (
                    len(matched_retrieval) / top_k
                    if relevant
                    else None
                ),
                "recall_at_k": (
                    len(matched_retrieval) / len(relevant)
                    if relevant
                    else None
                ),
                "reciprocal_rank_at_k": (
                    1 / first_rank
                    if first_rank
                    else 0
                    if relevant
                    else None
                ),
                "context_precision": (
                    len(matched_context) / len(selected_ids)
                    if relevant and selected_ids
                    else None
                ),
                "context_recall": (
                    len(matched_context) / len(relevant)
                    if relevant
                    else None
                ),
                "fallback_correct": actual_context == expected_context,
                "context_decision": prepared.decision,
                "context_reason": prepared.reason,
                "selected_chunk_ids": [
                    source.chunk.chunk_id
                    for source in prepared.sources
                ],
                "excluded": [
                    item.model_dump()
                    for item in prepared.excluded
                ],
                "candidates": [
                    _candidate(result)
                    for result in response.results
                ],
            }

        outcomes.append(outcome)

    _current, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    fallback_accuracy = {
        method: sum(
            outcome[method]["fallback_correct"]
            for outcome in outcomes
        ) / len(outcomes)
        for method in services
    }

    return {
        "methodology": {
            "type": "deterministic_labelled_chunk_overlap",
            "precision_limitation": (
                "Unlabelled but useful chunks count as non-relevant. "
                "Human chunk review is required before interpreting this "
                "as standard context precision."
            ),
            "memory_measurement": (
                "Peak Python allocations only; native Torch and Chroma "
                "memory are not included."
            ),
        },
        "top_k": top_k,
        "baseline": {
            "questions": len(cases),
            "parent_chunks": len(chunks),
            "active_manifest": index._manifest().model_dump(),
        },
        "dense_metrics": _aggregate(outcomes, "dense"),
        "hybrid_metrics": _aggregate(outcomes, "hybrid"),
        "fallback_accuracy": fallback_accuracy,
        "peak_python_allocated_bytes": peak_memory,
        "outcomes": outcomes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare dense and hybrid retrieval without provider APIs.",
    )
    parser.add_argument(
        "--questions",
        type=Path,
        default=DEFAULT_QUESTIONS,
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS,
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="Print compact candidate details for a selected case ID.",
    )
    args = parser.parse_args(argv)

    cases = json.loads(
        args.questions.read_text(encoding="utf-8"),
    )["cases"]
    report = compare(
        _load_chunks(args.corpus),
        cases,
        args.top_k,
    )
    if args.output is not None:
        write_json_atomic(args.output, report)
    misses = {
        method: [
            outcome["id"]
            for outcome in report["outcomes"]
            if outcome["answerable"]
            and outcome[method]["first_relevant_rank"] is None
        ]
        for method in ("dense", "hybrid")
    }
    selected_outcomes = []
    cases_by_id = {
        case["id"]: case
        for case in cases
    }
    for outcome in report["outcomes"]:
        if not args.case_ids or outcome["id"] not in args.case_ids:
            continue
        selected_outcomes.append({
            "id": outcome["id"],
            "language": outcome["language"],
            "question": outcome["question"],
            "expected_source": cases_by_id[outcome["id"]].get("source"),
            "expected_evidence": cases_by_id[outcome["id"]].get("evidence", []),
            "labelled_relevant_chunk_ids": outcome[
                "labelled_relevant_chunk_ids"
            ],
            "dense": {
                "first_relevant_rank": outcome["dense"][
                    "first_relevant_rank"
                ],
                "candidates": outcome["dense"]["candidates"],
            },
            "hybrid": {
                "first_relevant_rank": outcome["hybrid"][
                    "first_relevant_rank"
                ],
                "candidates": outcome["hybrid"]["candidates"],
            },
        })
    print(json.dumps(
        {
            "dense_metrics": report["dense_metrics"],
            "hybrid_metrics": report["hybrid_metrics"],
            "fallback_accuracy": report["fallback_accuracy"],
            "peak_python_allocated_bytes": report[
                "peak_python_allocated_bytes"
            ],
            "missed_answerable_cases": misses,
            "selected_outcomes": selected_outcomes,
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
