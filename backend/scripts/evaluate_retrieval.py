import argparse
from collections import defaultdict
import json
import logging
from pathlib import Path
import re
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(BACKEND_DIR))

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from app.models.document_chunk import DocumentChunk
from app.models.retrieval import LanguagePreference, RetrievalRequest
from app.rag.ingestion import write_json_atomic
from app.rag.retrieval import RetrievalService, get_retrieval_service
from app.services.firestore_client import get_firestore_client
from app.services.knowledge_base import KnowledgeBaseStore


logger = logging.getLogger(__name__)
DEFAULT_QUESTIONS = BACKEND_DIR / "data" / "questions" / "retrieval_questions.json"


class DevelopmentQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    language: LanguagePreference
    question: str
    source: str | None
    evidence: list[str]

    @model_validator(mode="after")
    def validate_evidence(self):
        if ((self.source is None) != (not self.evidence)
                or any(not excerpt.strip() for excerpt in self.evidence)):
            raise ValueError("Positive cases need a source and evidence; negative cases need neither.")
        return self


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def evaluate(service: RetrievalService, chunks: list[DocumentChunk], cases: list[dict], top_k: int) -> dict:
    questions = TypeAdapter(list[DevelopmentQuestion]).validate_python(cases)
    if not questions or len({case.id for case in questions}) != len(questions):
        logger.error("Development set must contain unique question IDs.")
        raise ValueError("Empty development set or duplicate question IDs.")
    outcomes = []
    for case in questions:
        request = RetrievalRequest(question=case.question, language=case.language, top_k=top_k)
        relevant = {
            chunk.chunk_id for chunk in chunks
            if case.source is not None and Path(chunk.source).name == case.source
            and all(normalize_text(excerpt) in normalize_text(chunk.text) for excerpt in case.evidence)
        }
        if case.source is not None and not relevant:
            logger.error("Development evidence is missing from the published corpus for %s.", case.id)
            raise ValueError("Development set does not match published evidence.")
        response = service.retrieve(request)
        ids = [result.chunk.chunk_id for result in response.results]
        matched = relevant.intersection(ids)
        first_rank = next((i for i, identifier in enumerate(ids, 1) if identifier in relevant), None)
        outcomes.append({
            "id": case.id, "language": case.language, "question": response.question,
            "answerable_in_fixture": bool(relevant), "evidence_status": response.evidence_status,
            "relevant_chunk_ids": sorted(relevant), "retrieved_chunk_ids": ids,
            "first_relevant_rank": first_rank,
            "recall_at_k": len(matched) / len(relevant) if relevant else None,
            "precision_at_k": len(matched) / top_k if relevant else None,
            "reciprocal_rank_at_k": 1 / first_rank if first_rank else 0 if relevant else None,
            "retrieval_ms": response.retrieval_ms,
            "candidates": [{"rank": result.rank, "source": result.chunk.source, "score": result.score}
                           for result in response.results],
        })
    groups = defaultdict(list)
    for outcome in outcomes:
        if outcome["answerable_in_fixture"]:
            groups[outcome["language"]].append(outcome)
            groups["all"].append(outcome)
    return {
        "purpose": "development_passage_retrieval_not_held_out_evaluation",
        "top_k": top_k,
        "score_type": "cosine_similarity",
        "metrics": {
            language: {
                "questions": len(items),
                **{metric: sum(item[metric] for item in items) / len(items)
                   for metric in ("recall_at_k", "precision_at_k", "reciprocal_rank_at_k")},
            }
            for language, items in groups.items()
        },
        "unanswerable_note": "Negative queries still retrieve neighbours. Sufficiency/fallback is not implemented until I12.",
        "outcomes": outcomes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure development retrieval against predeclared policy excerpts.")
    parser.add_argument("--top-k", type=int, choices=range(1, 21), default=5)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        cases = json.loads(args.questions.read_text(encoding="utf-8"))["cases"]
        chunks = KnowledgeBaseStore(get_firestore_client()).read_index_snapshot()
        report = evaluate(get_retrieval_service(), chunks, cases, args.top_k)
        write_json_atomic(args.output, report)
        print(json.dumps(report["metrics"], indent=2))
        print(f"Development results saved to {args.output}")
        return 0
    except HTTPException as exc:
        logger.error("Development retrieval failed (HTTP %s).", exc.status_code)
        return 1
    except (OSError, ValueError, KeyError) as exc:
        logger.error("Development evaluation failed (%s).", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
