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


def _source_overlap(
    selected_sources: set[str],
    expected_sources: set[str],
) -> tuple[float, float]:
    """Calculate custom document-source overlap for retrieval evaluation."""

    matched = selected_sources & expected_sources
    precision = (
        len(matched) / len(selected_sources)
        if selected_sources
        else 0.0
    )
    recall = len(matched) / len(expected_sources)
    return round(precision, 4), round(recall, 4)


def _zero_score_reason(
    selected_sources: set[str],
    expected_sources: set[str],
    eligible: bool,
) -> str | None:
    """Explain unavailable and zero document-overlap results explicitly."""

    if not eligible:
        return "Unsupported-enquiry case; evaluated with fallback accuracy only."
    if not selected_sources:
        return "No chunks passed context selection for this answerable question."
    if not selected_sources & expected_sources:
        return "Selected documents did not match any reviewed expected document."
    return None


def _context_outcome(case: dict, prepared) -> dict:
    """Create one retrieval/context record per question, independent of models."""

    expected_sources = set(case["expected_sources"])
    selected_sources = {
        Path(source.chunk.source).name
        for source in prepared.sources
    }
    eligible = not case["expected_fallback"]
    precision = recall = None
    if eligible:
        precision, recall = _source_overlap(
            selected_sources,
            expected_sources,
        )

    selected_chunk_ids = {
        source.chunk.chunk_id
        for source in prepared.sources
    }
    # Store the exact ordered text supplied to generation in the local report.
    # Firestore aggregate records intentionally never receive this content.
    selected_contexts = [source.chunk.text for source in prepared.sources]
    context_fingerprint = hashlib.sha256(
        "\n\n".join(selected_contexts).encode("utf-8"),
    ).hexdigest()
    retrieved_chunks = []
    for result in prepared.retrieval.results:
        document_name = Path(result.chunk.source).name
        retrieved_chunks.append({
            "rank": result.rank,
            "chunk_id": result.chunk.chunk_id,
            "document": document_name,
            "similarity": result.score,
            "selected_for_context": (
                result.chunk.chunk_id in selected_chunk_ids
            ),
            # This is only a document-label-derived indication. The dataset has
            # no human chunk judgments, so it must not be called chunk relevance.
            "document_label_match": document_name in expected_sources,
            "relevance_judgment": "not_chunk_labelled",
        })

    legacy_expected = set(
        case.get("previous_expected_sources", case["expected_sources"]),
    )
    legacy_precision = (
        len(selected_sources & legacy_expected) / len(selected_sources)
        if selected_sources
        else 0.0
    )
    return {
        "case_id": case["id"],
        "language": case["language"],
        "category": case["category"],
        "question": case["question"],
        "expected_fallback": case["expected_fallback"],
        "context_metric_eligible": eligible,
        "expected_documents": sorted(expected_sources),
        "selected_documents": sorted(selected_sources),
        "source_review": case.get("source_review"),
        "reference_answer": case.get("expected_answer"),
        "decision": prepared.decision,
        "decision_reason": prepared.reason,
        "document_source_precision": precision,
        "document_source_recall": recall,
        "legacy_document_source_precision": round(
            legacy_precision,
            4,
        ),
        "zero_score_reason": _zero_score_reason(
            selected_sources,
            expected_sources,
            eligible,
        ),
        "retrieved_chunks": retrieved_chunks,
        "selected_contexts": selected_contexts,
        "context_fingerprint": context_fingerprint,
        "rank_aware_context_precision": None,
        "rank_aware_metric_note": (
            "Unavailable: this dataset labels documents, not individual chunks. "
            "A rank-aware score would require human chunk-level judgments."
        ),
    }


def _context_records(
    context_outcomes: list[dict],
    run_id: str,
    evaluated_at: datetime,
    dataset: dict,
    knowledge_base_version: str,
) -> list[dict]:
    """Aggregate each unique question once for Analytics persistence."""

    records = []
    for language in ("all", "en", "sw", "mixed"):
        items = [
            item
            for item in context_outcomes
            if language == "all" or item["language"] == language
        ]
        eligible = [
            item
            for item in items
            if item["context_metric_eligible"]
        ]
        fallback_correct = [
            (
                item["decision"] != "context_available"
            ) == item["expected_fallback"]
            for item in items
        ]
        records.append({
            "run_id": run_id,
            "evaluated_at": evaluated_at,
            "dataset_id": dataset["dataset_id"],
            "dataset_version": dataset["version"],
            "knowledge_base_version": knowledge_base_version,
            "language": language,
            "sample_size": len(eligible),
            "context_sample_size": len(eligible),
            "fallback_sample_size": len(items),
            "context_precision": (
                sum(
                    item["document_source_precision"]
                    for item in eligible
                ) / len(eligible)
                if eligible
                else None
            ),
            "context_recall": (
                sum(
                    item["document_source_recall"]
                    for item in eligible
                ) / len(eligible)
                if eligible
                else None
            ),
            "fallback_accuracy": (
                sum(fallback_correct) / len(fallback_correct)
                if fallback_correct
                else None
            ),
            "method": "custom_document_source_overlap_unique_questions",
        })
    return records


def _persist_context_records(records: list[dict]) -> None:
    """Persist one context record per language, never one per AI model."""

    client = get_firestore_client()
    for record in records:
        document_id = f"{record['run_id']}-{record['language']}"
        with firestore_operation():
            client.collection("context_evaluations").document(
                document_id,
            ).set(record)


def run_context_only(
    dataset: dict,
    persist: bool = False,
    knowledge_base_version: str | None = None,
) -> dict:
    """Evaluate retrieval/context selection without calling any AI provider."""

    settings = get_settings()
    retrieval = get_retrieval_service()
    run_id = f"{dataset['dataset_id']}-context-{uuid4().hex[:10]}"
    evaluated_at = datetime.now(timezone.utc)
    knowledge_base_version = knowledge_base_version or _kb_version()
    context_outcomes = []
    for case in dataset["cases"]:
        response = retrieval.retrieve(RetrievalRequest(
            question=case["question"],
            language=case["language"],
            top_k=settings.retrieval_top_k,
        ))
        prepared = build_context(response, settings)
        context_outcomes.append(_context_outcome(case, prepared))

    records = _context_records(
        context_outcomes,
        run_id,
        evaluated_at,
        dataset,
        knowledge_base_version,
    )
    if persist:
        _persist_context_records(records)
    eligible = [
        item
        for item in context_outcomes
        if item["context_metric_eligible"]
    ]
    original_precision = sum(
        item["legacy_document_source_precision"]
        for item in context_outcomes
    ) / len(context_outcomes)
    corrected_precision = sum(
        item["document_source_precision"]
        for item in eligible
    ) / len(eligible)
    return {
        "run_id": run_id,
        "dataset": dataset["dataset_id"],
        "knowledge_base_version": knowledge_base_version,
        "context_methodology": {
            "metric_name": "document_source_overlap",
            "unique_questions": len(context_outcomes),
            "eligible_answerable_questions": sum(
                item["context_metric_eligible"]
                for item in context_outcomes
            ),
            "chunk_relevance_labels_available": False,
        },
        "context_records": [
            {
                **record,
                "evaluated_at": record["evaluated_at"].isoformat(),
            }
            for record in records
        ],
        "context_outcomes": context_outcomes,
        "methodology_comparison": {
            "original_document_source_precision": round(
                original_precision,
                6,
            ),
            "corrected_document_source_precision": round(
                corrected_precision,
                6,
            ),
            "retrieval_configuration_changed": False,
            "explanation": (
                "The difference is caused by reviewed source-label corrections "
                "and excluding unsupported cases, not retrieval optimisation."
            ),
        },
    }


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
    context_outcomes: list[dict] = []
    for case in dataset["cases"]:
        retrieval_response = retrieval.retrieve(RetrievalRequest(
            question=case["question"], language=case["language"], top_k=settings.retrieval_top_k,
        ))
        prepared = build_context(retrieval_response, settings)
        context_result = _context_outcome(case, prepared)
        context_outcomes.append(context_result)
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
            source_names = set(context_result["selected_documents"])
            outcomes.append({
                "case_id": case["id"], "model_id": model_id, "language": case["language"],
                "category": case["category"], "answer": answer, "decision": prepared.decision,
                "generation_called": generation_called, "latency_ms": latency_ms, "failure": failure,
                "correctness_proxy": None if failure else _overlap(answer, case["expected_answer"]),
                "answer_relevance": None if failure else _overlap(answer, case["question"]),
                "faithfulness_proxy": None if failure else (1.0 if prepared.sources and generation_called else 0.0),
                "context_metric_eligible": context_result["context_metric_eligible"],
                "context_precision": context_result["document_source_precision"],
                "context_recall": context_result["document_source_recall"],
                "fallback_correct": fallback_correct, "retrieved_sources": sorted(source_names),
                "context_fingerprint": context_result["context_fingerprint"],
            })
    grouped = defaultdict(list)
    for item in outcomes:
        grouped[(item["model_id"], item["language"])].append(item)
    results = []
    for (model_id, language), items in sorted(grouped.items()):
        valid = [item for item in items if not item["failure"]]
        context_items = [
            item
            for item in items
            if item["context_metric_eligible"]
        ]
        record = {
            "run_id": run_id, "evaluated_at": evaluated_at.isoformat(), "dataset_id": dataset["dataset_id"],
            "dataset_version": dataset["version"], "knowledge_base_version": knowledge_base_version,
            "model_id": model_id, "language": language, "sample_size": len(items),
            "model_settings": {
                "provider": configured[model_id].provider,
                "upstream_model": configured[model_id].upstream_model,
                "system_instruction_version": "kq-grounded-v1",
            },
            "evaluation_methods": {
                "context_precision": "custom_document_source_overlap",
                "context_recall": "custom_document_source_overlap",
                "fallback_accuracy": "labelled_supported_and_unsupported_enquiries",
                "faithfulness": "binary_proxy_not_validated_for_analytics",
                "answer_relevance": "lexical_proxy_not_validated_for_analytics",
            },
            "correctness": sum(i["correctness_proxy"] or 0 for i in valid) / len(valid) if valid else None,
            "faithfulness": sum(i["faithfulness_proxy"] or 0 for i in valid) / len(valid) if valid else None,
            "answer_relevance": sum(i["answer_relevance"] or 0 for i in valid) / len(valid) if valid else None,
            "context_sample_size": len(context_items),
            "fallback_sample_size": len(items),
            "context_precision": sum(
                i["context_precision"]
                for i in context_items
            ) / len(context_items) if context_items else None,
            "context_recall": sum(
                i["context_recall"]
                for i in context_items
            ) / len(context_items) if context_items else None,
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
    eligible_context = [
        item
        for item in context_outcomes
        if item["context_metric_eligible"]
    ]
    original_precision = sum(
        item["legacy_document_source_precision"]
        for item in context_outcomes
    ) / len(context_outcomes)
    corrected_precision = sum(
        item["document_source_precision"]
        for item in eligible_context
    ) / len(eligible_context)
    context_records = _context_records(
        context_outcomes,
        run_id,
        evaluated_at,
        dataset,
        knowledge_base_version,
    )
    if persist:
        _persist_context_records(context_records)
    return {
        "run_id": run_id,
        "dataset": dataset["dataset_id"],
        "knowledge_base_version": knowledge_base_version,
        "results": results,
        "outcomes": outcomes,
        "context_outcomes": context_outcomes,
        "context_records": [
            {
                **record,
                "evaluated_at": record["evaluated_at"].isoformat(),
            }
            for record in context_records
        ],
        "context_methodology": {
            "metric_name": "document_source_overlap",
            "unique_questions": len(context_outcomes),
            "eligible_answerable_questions": len(eligible_context),
            "unsupported_questions_excluded": (
                len(context_outcomes) - len(eligible_context)
            ),
            "chunk_relevance_labels_available": False,
        },
        "methodology_comparison": {
            "original_document_source_precision": round(
                original_precision,
                6,
            ),
            "corrected_document_source_precision": round(
                corrected_precision,
                6,
            ),
            "retrieval_configuration_changed": False,
            "explanation": (
                "The difference is caused by reviewed source-label corrections "
                "and excluding unsupported cases, not retrieval optimisation."
            ),
        },
        "selection_guidance": "Choose the configured model with the strongest reviewed grounded correctness and language quality, acceptable fallback accuracy, lowest failure rate, and acceptable latency. No automatic winner is claimed.",
        "final_model_selection": None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate configured KQ models on the versioned dataset.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--models", nargs="*")
    parser.add_argument(
        "--context-only",
        action="store_true",
        help="Evaluate unique retrieval/context cases without model API calls.",
    )
    parser.add_argument(
        "--knowledge-base-version",
        help=(
            "Reuse a previously recorded index version for an offline "
            "context-only run without reading Firestore metadata."
        ),
    )
    parser.add_argument("--persist", action="store_true", help="Save aggregate records to Firestore for administrators.")
    args = parser.parse_args(argv)
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    report = (
        run_context_only(
            dataset,
            args.persist,
            args.knowledge_base_version,
        )
        if args.context_only
        else run(dataset, args.models, args.persist)
    )
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = report.get("results", report.get("context_records", []))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
