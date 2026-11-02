"""Run a reproducible, evidence-controlled comparison of configured generators (I24).

The evaluator retrieves once per case, then gives the identical rendered context and
system instruction to every model. It writes a JSON report and, with ``--persist``,
one aggregate record per model/language to ``model_evaluations`` for the admin UI.
"""

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings
from app.models.retrieval import RetrievalRequest
from app.rag.context import build_context
from app.rag.generation import GenerationProviderError, get_generation_service
from app.rag.retrieval import get_retrieval_service
from app.services.firestore_client import FIRESTORE_READ_TIMEOUT, firestore_operation, get_firestore_client

DEFAULT_DATASET = BACKEND_DIR / "data" / "evaluation" / "kq_evaluation_v1.json"
SYSTEM_INSTRUCTION = (
    "You are Kenya Airways customer support. Answer only from the supplied evidence, "
    "preserve important qualifications, and use the requested language. If evidence is "
    "insufficient, clearly say so rather than inventing policy."
)


def _kb_version() -> str:
    """Fingerprint active document versions so a result cannot be mistaken for another KB."""
    with firestore_operation():
        snapshots = get_firestore_client().collection("documents").stream(timeout=FIRESTORE_READ_TIMEOUT)
        versions = sorted(
            f"{item.id}:{(item.to_dict() or {}).get('active', {}).get('version', '')}"
            for item in snapshots
        )
    return hashlib.sha256("\n".join(versions).encode()).hexdigest()[:16] or "empty-kb"


def _overlap(answer: str, expected: str) -> float:
    """A transparent lexical proxy; human/LLM review remains required for final claims."""
    expected_words = {word.lower().strip(".,:;!?()") for word in expected.split() if len(word) > 2}
    answer_words = {word.lower().strip(".,:;!?()") for word in answer.split()}
    return round(len(expected_words & answer_words) / len(expected_words), 4) if expected_words else 0.0


def run(dataset: dict, model_ids: list[str] | None = None, persist: bool = False) -> dict:
    settings = get_settings()
    retrieval = get_retrieval_service()
    generation = get_generation_service()
    configured = {item.model_id: item for item in generation.configured_models()}
    selected = model_ids or list(configured)
    unavailable = [model_id for model_id in selected if model_id not in configured or not configured[model_id].configured]
    if unavailable:
        raise ValueError(f"Models are not configured: {', '.join(unavailable)}")
    run_id = f"{dataset['dataset_id']}-{uuid4().hex[:10]}"
    evaluated_at = datetime.now(timezone.utc)
    knowledge_base_version = _kb_version()
    outcomes: list[dict] = []
    for case in dataset["cases"]:
        prepared = build_context(retrieval.retrieve(RetrievalRequest(
            question=case["question"], language=case["language"], top_k=settings.retrieval_top_k,
        )), settings)
        # The context and retrieval decision are shared by all models for this case.
        for model_id in selected:
            started = perf_counter()
            failure = None
            generation_called = prepared.decision == "context_available"
            answer = prepared.message or ""
            if generation_called:
                prompt = f"Language: {case['language']}\nEvidence:\n{prepared.context}\n\nQuestion: {case['question']}"
                try:
                    answer = generation.generate(prompt, model_id=model_id, system_instruction=SYSTEM_INSTRUCTION).text
                except GenerationProviderError as error:
                    failure = f"provider_{error.status_code}"
            latency_ms = round((perf_counter() - started) * 1000, 3)
            fallback_correct = (not case["expected_fallback"]) if failure else (prepared.decision != "context_available") == case["expected_fallback"]
            source_names = {Path(source.chunk.source).name for source in prepared.sources}
            expected_sources = set(case["expected_sources"])
            context_precision = len(source_names & expected_sources) / len(source_names) if source_names else 0.0
            context_recall = len(source_names & expected_sources) / len(expected_sources) if expected_sources else (1.0 if not source_names else 0.0)
            outcomes.append({
                "case_id": case["id"], "model_id": model_id, "language": case["language"],
                "category": case["category"], "answer": answer, "decision": prepared.decision,
                "generation_called": generation_called, "latency_ms": latency_ms, "failure": failure,
                "correctness_proxy": None if failure else _overlap(answer, case["expected_answer"]),
                "answer_relevance": None if failure else _overlap(answer, case["question"]),
                "faithfulness_proxy": None if failure else (1.0 if prepared.sources and generation_called else 0.0),
                "context_precision": round(context_precision, 4), "context_recall": round(context_recall, 4),
                "fallback_correct": fallback_correct, "retrieved_sources": sorted(source_names),
            })
    grouped = defaultdict(list)
    for item in outcomes:
        grouped[(item["model_id"], item["language"])].append(item)
    results = []
    for (model_id, language), items in sorted(grouped.items()):
        valid = [item for item in items if not item["failure"]]
        record = {
            "run_id": run_id, "evaluated_at": evaluated_at.isoformat(), "dataset_id": dataset["dataset_id"],
            "dataset_version": dataset["version"], "knowledge_base_version": knowledge_base_version,
            "model_id": model_id, "language": language, "sample_size": len(items),
            "model_settings": {"provider": configured[model_id].provider, "upstream_model": configured[model_id].upstream_model, "system_instruction_version": "kq-grounded-v1"},
            "correctness": sum(i["correctness_proxy"] or 0 for i in valid) / len(valid) if valid else None,
            "faithfulness": sum(i["faithfulness_proxy"] or 0 for i in valid) / len(valid) if valid else None,
            "answer_relevance": sum(i["answer_relevance"] or 0 for i in valid) / len(valid) if valid else None,
            "context_precision": sum(i["context_precision"] for i in items) / len(items),
            "context_recall": sum(i["context_recall"] for i in items) / len(items),
            "fallback_accuracy": sum(i["fallback_correct"] for i in items) / len(items),
            "language_quality": None,
            "average_response_ms": sum(i["latency_ms"] for i in items) / len(items),
            "failure_rate": sum(bool(i["failure"]) for i in items) / len(items),
        }
        results.append(record)
        if persist:
            # Firestore receives a native aware datetime; the JSON report uses ISO text.
            firestore_record = {**record, "evaluated_at": evaluated_at}
            with firestore_operation():
                get_firestore_client().collection("model_evaluations").document(f"{run_id}-{model_id}-{language}").set(firestore_record)
    return {
        "run_id": run_id,
        "dataset": dataset["dataset_id"],
        "knowledge_base_version": knowledge_base_version,
        "results": results,
        "outcomes": outcomes,
        "selection_guidance": "Choose the configured model with the strongest reviewed grounded correctness and language quality, acceptable fallback accuracy, lowest failure rate, and acceptable latency. No automatic winner is claimed.",
        "final_model_selection": None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate configured KQ models on the versioned dataset.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--models", nargs="*")
    parser.add_argument("--persist", action="store_true", help="Save aggregate records to Firestore for administrators.")
    args = parser.parse_args(argv)
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    report = run(dataset, args.models, args.persist)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["results"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
