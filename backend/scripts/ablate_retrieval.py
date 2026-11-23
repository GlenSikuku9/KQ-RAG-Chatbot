"""Measure retrieval components without changing production configuration."""

import json
from pathlib import Path
import sys
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(BACKEND_DIR))

from scripts.compare_retrieval import _load_chunks, _relevant_chunk_ids
from app.rag.retrieval import get_retrieval_service, normalized_search_query
from app.rag.vector_index import _bm25_scores


QUESTIONS = (
    BACKEND_DIR
    / "data"
    / "questions"
    / "retrieval_questions.json"
)
CORPUS = (
    BACKEND_DIR
    / "data"
    / "processed"
    / "kq_document_chunks.json"
)


def metrics(outcomes: list[dict]) -> dict:
    answerable = [item for item in outcomes if item["relevant_count"]]
    return {
        "questions": len(answerable),
        "precision_at_5": sum(
            item["matched"] / 5
            for item in answerable
        ) / len(answerable),
        "recall_at_5": sum(
            item["matched"] / item["relevant_count"]
            for item in answerable
        ) / len(answerable),
        "mrr_at_5": sum(
            1 / item["first_rank"] if item["first_rank"] else 0
            for item in answerable
        ) / len(answerable),
    }


def evaluate(search, cases: list[dict], chunks: list) -> dict:
    outcomes = []
    for case in cases:
        relevant = _relevant_chunk_ids(case, chunks)
        hits = search(case)
        identifiers = [hit.chunk.chunk_id for hit in hits]
        matched = relevant & set(identifiers)
        first_rank = next((
            rank
            for rank, identifier in enumerate(identifiers, 1)
            if identifier in relevant
        ), None)
        outcomes.append({
            "id": case["id"],
            "language": case["language"],
            "relevant_count": len(relevant),
            "matched": len(matched),
            "first_rank": first_rank,
        })
    result = {"all": metrics(outcomes)}
    for language in ("en", "sw", "mixed"):
        result[language] = metrics([
            item
            for item in outcomes
            if item["language"] == language
        ])
    result["misses"] = [
        item["id"]
        for item in outcomes
        if item["relevant_count"] and item["first_rank"] is None
    ]
    return result


def main() -> int:
    cases = json.loads(QUESTIONS.read_text(encoding="utf-8"))["cases"]
    chunks = _load_chunks(CORPUS)
    service = get_retrieval_service()
    index = service.index
    settings = service.settings
    index.embedder.embed_query("ablation warm-up")

    def dense(original: bool):
        return lambda case: index.search(
            case["question"] if original else normalized_search_query(
                case["question"],
                case["language"],
            ),
            5,
        )

    def hybrid(original: bool):
        return lambda case: index.search_hybrid(
            case["question"] if original else normalized_search_query(
                case["question"],
                case["language"],
            ),
            5,
            dense_candidates=settings.hybrid_dense_candidates,
            lexical_candidates=settings.hybrid_lexical_candidates,
            rrf_k=settings.hybrid_rrf_k,
        )

    report = {
        "warning": (
            "Development-set ablation only. Do not tune using held-out data."
        ),
        "dense_original_query": evaluate(dense(True), cases, chunks),
        "dense_expanded_query": evaluate(dense(False), cases, chunks),
        "hybrid_original_query": evaluate(hybrid(True), cases, chunks),
        "hybrid_expanded_query": evaluate(hybrid(False), cases, chunks),
    }

    def body_only(query, documents):
        return _bm25_scores(
            query,
            documents,
            include_taxonomy=False,
            include_heading_signal=False,
        )

    with patch("app.rag.vector_index._bm25_scores", body_only):
        report["hybrid_without_taxonomy_or_heading"] = evaluate(
            hybrid(False),
            cases,
            chunks,
        )

    output = (
        BACKEND_DIR
        / "data"
        / "evaluation"
        / "retrieval-ablation.json"
    )
    output.write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
