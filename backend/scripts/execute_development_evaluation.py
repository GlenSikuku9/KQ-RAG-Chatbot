"""Execute the authorised development experiment using the existing framework.

Retrieval/context is prepared once per question and replayed unchanged across
the three candidates. Responses pass through the production grounded-answer
service, including language handling, link sanitization, and citation checks.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import secrets
import statistics
import sys
from time import perf_counter, sleep
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings
from app.rag.context import get_context_service
from app.rag.generation import GenerationProviderError, get_generation_service
from app.rag.grounded_generation import GroundedAnswerService
from app.rag.ingestion import write_json_atomic
from scripts.experimental_evaluation import (
    CANDIDATE_MODEL_IDS,
    DEFAULT_DEVELOPMENT_DATASET,
    EXPECTED_UPSTREAM_MODELS,
    build_request,
    evidence_metrics,
    validate_dataset,
)


EXPECTED_RESPONSE_CLASSES = {
    "clarify": {"clarification"},
    "partial_answer_or_clarify": {"answer", "clarification"},
    "unsupported_private_data": {"fallback"},
    "route_to_live_flight_workflow_or_clarify": {
        "fallback",
        "clarification",
    },
    "unsupported_transaction": {"fallback"},
}


class ReplayContextService:
    """Return the exact precomputed context without altering production logic."""

    def __init__(self):
        self.prepared = None

    def prepare(self, request):
        if self.prepared is None:
            raise RuntimeError("No evaluation context is selected.")
        return self.prepared


def percentile_95(values: list[float]) -> float | None:
    """Return the observed nearest-rank 95th percentile."""

    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def fallback_accuracy(case: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    """Compare reviewed response class with the backend's final decision."""

    if outcome["status"] == "failed" or not outcome.get("decision"):
        return {
            "value": None,
            "applicable": False,
            "missing_reason": "Technical failure is not a chatbot fallback.",
        }
    decision = outcome["decision"]
    observed = (
        "clarification"
        if decision == "clarification_required"
        else "fallback"
        if decision in {"fallback", "non_rag", "unsupported"}
        else "answer"
    )
    expected = EXPECTED_RESPONSE_CLASSES.get(
        case["expected_behavior"],
        {"answer"},
    )
    return {
        "value": float(observed in expected),
        "applicable": True,
        "expected_classes": sorted(expected),
        "observed_class": observed,
        "method": "reviewed_expected_class_vs_final_backend_decision",
        "missing_reason": None,
    }


def metric_summary(rows: list[dict[str, Any]], metric_name: str) -> dict[str, Any]:
    """Aggregate deterministic metrics without converting missing values to zero."""

    records = [row["shared_pipeline"]["metrics"][metric_name] for row in rows]
    applicable = [record for record in records if record["applicable"]]
    values = [record["value"] for record in applicable if record["value"] is not None]
    return {
        "value": statistics.fmean(values) if values else None,
        "applicable": len(applicable),
        "scored": len(values),
        "missing": len(applicable) - len(values),
    }


def validate_candidate_settings(settings) -> dict[str, Any]:
    """Validate candidates without reading the unrelated stored default."""

    configured = {model.model_id: model for model in settings.generation_models}
    metadata = {}
    for model_id in CANDIDATE_MODEL_IDS:
        model = configured.get(model_id)
        if model is None or not model.api_key:
            raise ValueError(f"Candidate is not configured: {model_id}")
        if model.upstream_model != EXPECTED_UPSTREAM_MODELS[model_id]:
            raise ValueError(f"Unexpected upstream model for {model_id}")
        metadata[model_id] = {
            "provider": model.provider,
            "upstream_model": model.upstream_model,
            "max_output_tokens": model.max_output_tokens,
            "timeout_seconds": model.timeout_seconds,
        }
    return metadata


def prepare_cases(dataset: dict[str, Any]) -> tuple[dict[str, Any], float]:
    """Warm the local index, then measure one shared context per question."""

    context_service = get_context_service()
    warmup_started = perf_counter()
    context_service.prepare(build_request(dataset["cases"][0], 5))
    warmup_ms = round((perf_counter() - warmup_started) * 1000, 3)
    prepared = {}
    for index, case in enumerate(dataset["cases"], 1):
        started = perf_counter()
        context = context_service.prepare(build_request(case, 5))
        preparation_ms = (perf_counter() - started) * 1000
        context_build_ms = max(
            0.0,
            preparation_ms - context.retrieval.retrieval_ms,
        )
        prepared[case["case_id"]] = {
            "object": context,
            "context_build_ms": round(context_build_ms, 3),
        }
        print(f"[context {index}/45] {case['case_id']}", flush=True)
    return prepared, warmup_ms


def serialize_case_context(case: dict[str, Any], prepared, context_build_ms: float):
    """Preserve retrieved parents, supplied evidence, and deterministic scores."""

    retrieved_ids = [hit.chunk.chunk_id for hit in prepared.retrieval.results]
    selected_ids = [source.chunk.chunk_id for source in prepared.sources]
    retrieval_scores, retrieval_matches = evidence_metrics(
        case,
        retrieved_ids,
        5,
        precision_denominator=5,
        scope="retrieval_at_k",
    )
    if selected_ids:
        context_scores, context_matches = evidence_metrics(
            case,
            selected_ids,
            len(selected_ids),
            precision_denominator=len(selected_ids),
            scope="actual_supplied_context",
        )
    else:
        context_scores, context_matches = evidence_metrics(
            case,
            [],
            0,
            precision_denominator=1,
            scope="actual_supplied_context",
        )
    return {
        "retrieval_ms": prepared.retrieval.retrieval_ms,
        "context_build_ms": context_build_ms,
        "retrieved_chunks": [
            {
                "rank": hit.rank,
                "parent_chunk_id": hit.chunk.chunk_id,
                "document": Path(hit.chunk.source).name,
                "section": hit.chunk.section,
                "text": hit.chunk.text,
                "score": hit.score,
            }
            for hit in prepared.retrieval.results
        ],
        "complete_supplied_evidence": [
            {
                "citation_id": source.citation_id,
                "parent_chunk_id": source.chunk.chunk_id,
                "text": source.chunk.text,
                "retrieval_rank": source.retrieval_rank,
                "score": source.score,
            }
            for source in prepared.sources
        ],
        "rendered_context": prepared.context,
        "decision": prepared.decision,
        "retrieval_criterion_matches": retrieval_matches,
        "context_criterion_matches": context_matches,
        "metrics": {
            "precision_at_5": retrieval_scores["precision"],
            "criterion_recall_at_5": retrieval_scores["recall"],
            "mrr_at_5": retrieval_scores["mrr"],
            "context_precision": context_scores["precision"],
            "context_recall": context_scores["recall"],
        },
    }


def aggregate(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate reliability and steady-state latency by model and language."""

    result = {}
    for model_id in CANDIDATE_MODEL_IDS:
        model_rows = [row for row in outcomes if row["model_id"] == model_id]
        result[model_id] = {}
        for scope in ("all", "en", "sw", "mixed"):
            rows = (
                model_rows
                if scope == "all"
                else [row for row in model_rows if row["language"] == scope]
            )
            failures = [row for row in rows if row["status"] == "failed"]
            steady = [
                row["response_time_ms"]
                for row in rows
                if row["status"] == "completed"
                and not row["model_warmup_observation"]
            ]
            fallback = [
                row["fallback_accuracy"]
                for row in rows
                if row["fallback_accuracy"]["applicable"]
            ]
            fallback_values = [
                record["value"]
                for record in fallback
                if record["value"] is not None
            ]
            result[model_id][scope] = {
                "sample_size": len(rows),
                "failure_rate": (
                    len(failures) / len(rows) if rows else None
                ),
                "failure_count": len(failures),
                "steady_state_latency_sample_size": len(steady),
                "average_response_time_ms": (
                    statistics.fmean(steady) if steady else None
                ),
                "median_response_time_ms": (
                    statistics.median(steady) if steady else None
                ),
                "p95_response_time_ms": percentile_95(steady),
                "fallback_accuracy": (
                    statistics.fmean(fallback_values)
                    if fallback_values
                    else None
                ),
                "fallback_applicable": len(fallback),
                "fallback_scored": len(fallback_values),
                "fallback_missing": len(fallback) - len(fallback_values),
                "precision_at_5": metric_summary(rows, "precision_at_5"),
                "criterion_recall_at_5": metric_summary(
                    rows,
                    "criterion_recall_at_5",
                ),
                "mrr_at_5": metric_summary(rows, "mrr_at_5"),
                "context_precision": metric_summary(rows, "context_precision"),
                "context_recall": metric_summary(rows, "context_recall"),
            }
    return result


def export_csv(outcomes: list[dict[str, Any]], path: Path, aliases=None) -> None:
    """Export one auditable row per question/model."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "case_id", "model", "language", "question", "fixed_history",
        "reference_answer", "expected_behavior", "final_response", "decision",
        "citations", "retrieved_parent_chunk_ids", "supplied_parent_chunk_ids",
        "precision_at_5", "criterion_recall_at_5", "mrr_at_5",
        "context_precision", "context_recall", "fallback_accuracy",
        "response_time_ms", "retrieval_ms", "context_build_ms", "generation_ms",
        "model_warmup_observation", "status", "failure_type", "failure",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in outcomes:
            metrics = row["shared_pipeline"]["metrics"]
            writer.writerow({
                "case_id": row["case_id"],
                "model": aliases.get(row["model_id"], row["model_id"]) if aliases else row["model_id"],
                "language": row["language"],
                "question": row["question"],
                "fixed_history": row["fixed_history"],
                "reference_answer": row["reference_answer"],
                "expected_behavior": row["expected_behavior"],
                "final_response": row.get("final_response"),
                "decision": row.get("decision"),
                "citations": "|".join(row.get("citations", [])),
                "retrieved_parent_chunk_ids": "|".join(item["parent_chunk_id"] for item in row["shared_pipeline"]["retrieved_chunks"]),
                "supplied_parent_chunk_ids": "|".join(item["parent_chunk_id"] for item in row["shared_pipeline"]["complete_supplied_evidence"]),
                "precision_at_5": metrics["precision_at_5"]["value"],
                "criterion_recall_at_5": metrics["criterion_recall_at_5"]["value"],
                "mrr_at_5": metrics["mrr_at_5"]["value"],
                "context_precision": metrics["context_precision"]["value"],
                "context_recall": metrics["context_recall"]["value"],
                "fallback_accuracy": row["fallback_accuracy"]["value"],
                "response_time_ms": row.get("response_time_ms"),
                "retrieval_ms": row["shared_pipeline"]["retrieval_ms"],
                "context_build_ms": row["shared_pipeline"]["context_build_ms"],
                "generation_ms": row.get("generation_ms"),
                "model_warmup_observation": row["model_warmup_observation"],
                "status": row["status"],
                "failure_type": row.get("failure_type"),
                "failure": row.get("failure"),
            })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--blind-csv", type=Path, required=True)
    parser.add_argument("--identity-map", type=Path, required=True)
    parser.add_argument("--authorise-external-calls", action="store_true")
    parser.add_argument(
        "--groq-delay-seconds",
        type=float,
        default=0.0,
        help="Evaluation-only pacing outside measured response latency.",
    )
    args = parser.parse_args(argv)
    if not args.authorise_external_calls:
        raise PermissionError("Explicit external-call authorization is required.")

    dataset = json.loads(DEFAULT_DEVELOPMENT_DATASET.read_text(encoding="utf-8"))
    validate_dataset(dataset, "development")
    prepared, index_warmup_ms = prepare_cases(dataset)
    settings = get_settings()
    model_metadata = validate_candidate_settings(settings)
    generation = get_generation_service()
    replay = ReplayContextService()
    grounded = GroundedAnswerService(replay, generation)

    previous = []
    if args.output.exists():
        previous = json.loads(args.output.read_text(encoding="utf-8")).get("outcomes", [])
    # Resume successful work only. Infrastructure/provider failures are retried
    # on the next invocation and never become permanent fabricated results.
    done = {
        (row["case_id"], row["model_id"]): row
        for row in previous
        if row.get("status") == "completed"
    }
    outcomes = list(done.values())
    case_by_id = {case["case_id"]: case for case in dataset["cases"]}
    for outcome in outcomes:
        outcome["fallback_accuracy"] = fallback_accuracy(
            case_by_id[outcome["case_id"]],
            outcome,
        )
    warmed_models = {
        row["model_id"] for row in outcomes if row.get("generation_called")
    }
    total = len(dataset["cases"]) * len(CANDIDATE_MODEL_IDS)

    for case in dataset["cases"]:
        shared = prepared[case["case_id"]]
        serialized = serialize_case_context(
            case,
            shared["object"],
            shared["context_build_ms"],
        )
        for model_id in CANDIDATE_MODEL_IDS:
            if (case["case_id"], model_id) in done:
                continue
            replay.prepared = shared["object"]
            request = build_request(case, 5).model_copy(update={"model_id": model_id})
            warmup_observation = model_id not in warmed_models
            if model_id == "groq_gpt_oss_120b" and args.groq_delay_seconds:
                sleep(args.groq_delay_seconds)
            started = perf_counter()
            try:
                response = grounded.answer(request)
                elapsed = round((perf_counter() - started) * 1000, 3)
                outcome = {
                    "case_id": case["case_id"],
                    "semantic_group": case["semantic_group"],
                    "model_id": model_id,
                    "language": case["language"],
                    "question": case["question"],
                    "fixed_history": case.get("fixed_history"),
                    "reference_answer": case["reference_answer"],
                    "expected_behavior": case["expected_behavior"],
                    "status": "completed",
                    "final_response": response.answer,
                    "decision": response.decision,
                    "generation_called": response.generation_called,
                    "generation_ms": response.generation_ms,
                    "provider_duration_ms": response.model.provider_duration_ms if response.model else None,
                    "provider_attempt_count": response.model.attempt_count if response.model else 0,
                    "response_time_ms": round(serialized["retrieval_ms"] + serialized["context_build_ms"] + (response.generation_ms or 0), 3),
                    "citations": [source.citation_id for source in response.sources],
                    "sources": [source.model_dump(mode="json") for source in response.sources],
                    "shared_pipeline": serialized,
                    "model_warmup_observation": warmup_observation and response.generation_called,
                    "measured_replay_execution_ms": elapsed,
                    "failure": None,
                    "failure_type": None,
                }
            except GenerationProviderError as error:
                outcome = {
                    "case_id": case["case_id"], "semantic_group": case["semantic_group"],
                    "model_id": model_id, "language": case["language"],
                    "question": case["question"], "fixed_history": case.get("fixed_history"),
                    "reference_answer": case["reference_answer"], "expected_behavior": case["expected_behavior"],
                    "status": "failed", "final_response": None, "decision": None,
                    "generation_called": True, "generation_ms": None,
                    "response_time_ms": None, "citations": [], "sources": [],
                    "shared_pipeline": serialized,
                    "model_warmup_observation": warmup_observation,
                    "failure": error.detail, "failure_type": f"provider_{error.status_code}",
                }
            outcome["fallback_accuracy"] = fallback_accuracy(case, outcome)
            outcomes.append(outcome)
            if outcome["generation_called"]:
                warmed_models.add(model_id)
            report = {
                "schema_version": "kq-development-model-evaluation-v1",
                "run_id": "development-three-model-evaluation",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "dataset_partition": "development",
                "dataset_status": dataset["dataset_status"],
                "index_warmup_ms_excluded": index_warmup_ms,
                "candidate_models": model_metadata,
                "external_judges_used": False,
                "outcomes": outcomes,
                "aggregates": aggregate(outcomes),
            }
            write_json_atomic(args.output, report)
            print(f"[response {len(outcomes)}/{total}] {case['case_id']} / {model_id} - {outcome['status']}", flush=True)

    # Always refresh aggregates and exports, including a no-call finalization run.
    report = {
        "schema_version": "kq-development-model-evaluation-v1",
        "run_id": "development-three-model-evaluation",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_partition": "development",
        "dataset_status": dataset["dataset_status"],
        "index_warmup_ms_excluded": index_warmup_ms,
        "candidate_models": model_metadata,
        "external_judges_used": False,
        "outcomes": outcomes,
        "aggregates": aggregate(outcomes),
    }
    write_json_atomic(args.output, report)

    aliases = {}
    if args.identity_map.exists():
        aliases = json.loads(args.identity_map.read_text(encoding="utf-8"))["model_to_alias"]
    else:
        shuffled = list(CANDIDATE_MODEL_IDS)
        secrets.SystemRandom().shuffle(shuffled)
        aliases = {model_id: f"Model {chr(65 + index)}" for index, model_id in enumerate(shuffled)}
        write_json_atomic(args.identity_map, {"model_to_alias": aliases, "keep_private": True})
    export_csv(outcomes, args.csv)
    export_csv(outcomes, args.blind_csv, aliases)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
