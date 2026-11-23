"""Record raw retrieval and selected context for diagnostic questions."""

import argparse
import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(BACKEND_DIR))

from app.models.retrieval import RetrievalRequest
from app.rag.context import get_context_service
from app.rag.ingestion import write_json_atomic


DEFAULT_QUESTIONS = (
    BACKEND_DIR
    / "data"
    / "questions"
    / "retrieval_diagnostics.json"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    questions = json.loads(
        args.questions.read_text(encoding="utf-8"),
    )["questions"]
    service = get_context_service()
    outcomes = []

    # Warm the local model so recorded latency represents steady-state use.
    service.retrieval.index.embedder.embed_query("diagnostic warm-up")
    for item in questions:
        prepared = service.prepare(RetrievalRequest(
            question=item["question"],
            language=item["language"],
        ))
        outcomes.append({
            **item,
            "normalized_query": prepared.retrieval.normalized_query,
            "ranking_method": prepared.retrieval.ranking_method,
            "retrieval_ms": prepared.retrieval.retrieval_ms,
            "candidates": [
                result.model_dump(mode="json")
                for result in prepared.retrieval.results
            ],
            "selected_context": [
                source.model_dump(mode="json")
                for source in prepared.sources
            ],
            "excluded": [
                excluded.model_dump(mode="json")
                for excluded in prepared.excluded
            ],
            "decision": prepared.decision,
            "reason": prepared.reason,
            "context_chars": prepared.context_chars,
            "context_tokens": prepared.context_tokens,
        })

    write_json_atomic(args.output, {"outcomes": outcomes})
    print(f"Saved {len(outcomes)} diagnostic outcomes to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
