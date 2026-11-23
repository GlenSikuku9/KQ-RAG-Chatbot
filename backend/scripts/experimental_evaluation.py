"""Automated experimental evaluation for the final reviewed RAG dataset.

The module keeps retrieval/context measurements deterministic and provider-free.
Generation calls are an explicit later phase and support checkpoints so an
interrupted experiment does not repeat successful provider calls.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter
from typing import Any, Callable


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import Settings, get_settings
from app.models.generation import GroundedAnswerRequest
from app.models.retrieval import ConversationContextMessage, RetrievalRequest
from app.rag.context import build_context
from app.rag.generation import GenerationProviderError, GenerationService
from app.rag.grounded_generation import SYSTEM_INSTRUCTION, build_grounded_prompt
from app.rag.ingestion import write_json_atomic
from app.rag.retrieval import RetrievalService, get_retrieval_service
from app.services.conversation_context import resolve_conversation_query


DEFAULT_DEVELOPMENT_DATASET = (
    BACKEND_DIR
    / "data"
    / "evaluation"
    / "retrieval-evaluation-development-reviewed.json"
)
DEFAULT_TOP_K = 5
CANDIDATE_MODEL_IDS = (
    "openrouter_gemma_26b",
    "openrouter_gemma_31b",
    "groq_gpt_oss_120b",
)
EXPECTED_UPSTREAM_MODELS = {
    "openrouter_gemma_26b": "google/gemma-4-26b-a4b-it",
    "openrouter_gemma_31b": "google/gemma-4-31b-it",
    "groq_gpt_oss_120b": "openai/gpt-oss-120b",
}
FALLBACK_EXPECTATIONS = {
    "clarify": "clarification",
    "unsupported_private_data": "fallback",
    "route_to_live_flight_workflow_or_clarify": "fallback_or_clarification",
    "unsupported_transaction": "fallback",
}


def metric(
    *,
    value: float | None,
    applicable: bool,
    numerator: int | float | None,
    denominator: int | None,
    method: str,
    missing_reason: str | None = None,
) -> dict[str, Any]:
    """Create one explicit score record without turning missing into zero."""

    return {
        "value": value,
        "applicable": applicable,
        "numerator": numerator,
        "denominator": denominator,
        "method": method,
        "missing_reason": missing_reason,
    }


def parse_fixed_history(
    value: str | None,
    case_id: str,
) -> list[ConversationContextMessage]:
    """Convert reviewed USER/ASSISTANT history into production request objects."""

    if not value:
        return []

    messages: list[ConversationContextMessage] = []
    for line in value.splitlines():
        role, separator, text = line.partition(":")
        if not separator or not text.strip():
            raise ValueError(f"Malformed fixed history for {case_id}.")
        normalized_role = role.strip().upper()
        sender_type = {
            "USER": "customer",
            "ASSISTANT": "chatbot",
        }.get(normalized_role)
        if sender_type is None:
            raise ValueError(f"Unsupported fixed-history role for {case_id}.")
        messages.append(
            ConversationContextMessage(
                conversation_id=f"evaluation-{case_id}",
                sender_type=sender_type,
                message_text=text.strip(),
            ),
        )
    return messages


def build_request(case: dict[str, Any], top_k: int) -> GroundedAnswerRequest:
    """Apply the same deterministic history resolver used by passenger chat."""

    history = parse_fixed_history(
        case.get("fixed_history"),
        case["case_id"],
    )
    resolution = resolve_conversation_query(
        case["question"],
        history,
    )
    return GroundedAnswerRequest(
        question=case["question"],
        language=case["language"],
        conversation_context=list(resolution.context),
        resolved_question=resolution.resolved_question,
        top_k=top_k,
    )


def criterion_matches(
    criteria: list[dict[str, Any]],
    chunk_ids: list[str],
) -> list[dict[str, Any]]:
    """Match each mandatory criterion against its any-of equivalent parents."""

    ranks = {
        chunk_id: rank
        for rank, chunk_id in enumerate(chunk_ids, 1)
    }
    matches = []
    for criterion in criteria:
        acceptable = criterion["acceptable_parent_chunk_ids"]
        matched = sorted(
            (
                {
                    "parent_chunk_id": chunk_id,
                    "rank": ranks[chunk_id],
                }
                for chunk_id in acceptable
                if chunk_id in ranks
            ),
            key=lambda item: item["rank"],
        )
        matches.append(
            {
                "criterion_id": criterion["criterion_id"],
                "policy_text_locator": criterion["policy_text_locator"],
                "acceptable_parent_chunk_ids": acceptable,
                "satisfied": bool(matched),
                "matched_parent_chunks": matched,
            },
        )
    return matches


def evidence_metrics(
    case: dict[str, Any],
    chunk_ids: list[str],
    cutoff: int,
    *,
    precision_denominator: int,
    scope: str,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Score chunk precision and adapted criterion recall for one ordered list."""

    eligibility = case["metric_eligibility"]["retrieval_and_context_recall"]
    if eligibility != "eligible":
        reason = "Behaviour-only case has no mandatory static-policy evidence."
        unavailable = {
            "precision": metric(
                value=None,
                applicable=False,
                numerator=None,
                denominator=None,
                method=f"{scope}_chunk_precision",
                missing_reason=reason,
            ),
            "recall": metric(
                value=None,
                applicable=False,
                numerator=None,
                denominator=None,
                method=f"{scope}_mandatory_criterion_recall",
                missing_reason=reason,
            ),
            "mrr": metric(
                value=None,
                applicable=False,
                numerator=None,
                denominator=None,
                method=f"{scope}_first_criterion_match_reciprocal_rank",
                missing_reason=reason,
            ),
        }
        return unavailable, []

    criteria = case["mandatory_evidence_criteria"]
    if not criteria:
        raise ValueError(
            f"Eligible case {case['case_id']} has no mandatory criteria.",
        )

    considered = chunk_ids[:cutoff]
    matches = criterion_matches(criteria, considered)
    acceptable_ids = {
        chunk_id
        for criterion in criteria
        for chunk_id in criterion["acceptable_parent_chunk_ids"]
    }
    relevant_chunks = [
        chunk_id
        for chunk_id in considered
        if chunk_id in acceptable_ids
    ]
    satisfied = sum(item["satisfied"] for item in matches)
    first_rank = next(
        (
            rank
            for rank, chunk_id in enumerate(considered, 1)
            if chunk_id in acceptable_ids
        ),
        None,
    )
    scores = {
        "precision": metric(
            value=len(relevant_chunks) / precision_denominator,
            applicable=True,
            numerator=len(relevant_chunks),
            denominator=precision_denominator,
            method=f"{scope}_chunk_precision",
        ),
        "recall": metric(
            value=satisfied / len(criteria),
            applicable=True,
            numerator=satisfied,
            denominator=len(criteria),
            method=f"{scope}_mandatory_criterion_recall_any_equivalent_parent",
        ),
        "mrr": metric(
            value=(1 / first_rank if first_rank else 0.0),
            applicable=True,
            numerator=(1 if first_rank else 0),
            denominator=1,
            method=f"{scope}_first_criterion_match_reciprocal_rank",
        ),
    }
    return scores, matches


def serialize_retrieval(response, selected_ids: set[str]) -> list[dict[str, Any]]:
    """Preserve complete parent evidence and rank metadata for auditability."""

    return [
        {
            "rank": result.rank,
            "parent_chunk_id": result.chunk.chunk_id,
            "document": Path(result.chunk.source).name,
            "section": result.chunk.section,
            "text": result.chunk.text,
            "cosine_similarity": result.score,
            "dense_rank": result.dense_rank,
            "lexical_rank": result.lexical_rank,
            "fusion_score": result.fusion_score,
            "selected_for_context": result.chunk.chunk_id in selected_ids,
        }
        for result in response.results
    ]


def evaluate_offline_case(
    case: dict[str, Any],
    retrieval: RetrievalService,
    settings: Settings,
    top_k: int,
) -> dict[str, Any]:
    """Retrieve once and score the exact context supplied to generation."""

    request = build_request(case, top_k)
    retrieval_response = retrieval.retrieve(request)
    context_started = perf_counter()
    prepared = build_context(
        retrieval_response,
        settings,
    )
    context_ms = round(
        (perf_counter() - context_started) * 1000,
        3,
    )

    retrieved_ids = [
        result.chunk.chunk_id
        for result in retrieval_response.results
    ]
    selected_ids = [
        source.chunk.chunk_id
        for source in prepared.sources
    ]
    retrieval_scores, retrieval_matches = evidence_metrics(
        case,
        retrieved_ids,
        top_k,
        precision_denominator=top_k,
        scope="retrieval_at_k",
    )
    context_denominator = len(selected_ids)
    if context_denominator:
        context_scores, context_matches = evidence_metrics(
            case,
            selected_ids,
            len(selected_ids),
            precision_denominator=context_denominator,
            scope="actual_supplied_context",
        )
    elif case["metric_eligibility"]["retrieval_and_context_recall"] == "eligible":
        criteria_count = len(case["mandatory_evidence_criteria"])
        context_scores = {
            "precision": metric(
                value=None,
                applicable=False,
                numerator=None,
                denominator=0,
                method="actual_supplied_context_chunk_precision",
                missing_reason="No context was supplied to generation.",
            ),
            "recall": metric(
                value=0.0,
                applicable=True,
                numerator=0,
                denominator=criteria_count,
                method="actual_supplied_context_mandatory_criterion_recall_any_equivalent_parent",
            ),
            "mrr": metric(
                value=0.0,
                applicable=True,
                numerator=0,
                denominator=1,
                method="actual_supplied_context_first_criterion_match_reciprocal_rank",
            ),
        }
        context_matches = criterion_matches(
            case["mandatory_evidence_criteria"],
            [],
        )
    else:
        context_scores, context_matches = evidence_metrics(
            case,
            [],
            0,
            precision_denominator=1,
            scope="actual_supplied_context",
        )

    prompt = None
    prompt_tokens_estimate = 0
    if prepared.decision == "context_available":
        prompt = build_grounded_prompt(
            prepared,
            request,
        )
        prompt_tokens_estimate = max(1, len(prompt) // 4)

    return {
        "case_id": case["case_id"],
        "semantic_group": case["semantic_group"],
        "partition": case["partition"],
        "language": case["language"],
        "question": case["question"],
        "fixed_history": case.get("fixed_history"),
        "resolved_retrieval_query": request.resolved_question,
        "expected_behavior": case["expected_behavior"],
        "reference_answer": case["reference_answer"],
        "metric_eligibility": case["metric_eligibility"],
        "mandatory_evidence_criteria": case["mandatory_evidence_criteria"],
        "retrieval": {
            "top_k": top_k,
            "ranking_method": retrieval_response.ranking_method,
            "retrieval_ms": retrieval_response.retrieval_ms,
            "chunks": serialize_retrieval(
                retrieval_response,
                set(selected_ids),
            ),
            "criterion_matches": retrieval_matches,
            "metrics": {
                "precision_at_k": retrieval_scores["precision"],
                "criterion_recall_at_k": retrieval_scores["recall"],
                "mrr_at_k": retrieval_scores["mrr"],
            },
        },
        "context": {
            "decision": prepared.decision,
            "reason": prepared.reason,
            "message": prepared.message,
            "context_build_ms": context_ms,
            "context_chars": prepared.context_chars,
            "context_tokens_estimate": prepared.context_tokens,
            "selected_parent_chunk_ids": selected_ids,
            "selected_contexts": [
                source.chunk.text
                for source in prepared.sources
            ],
            "rendered_context": prepared.context,
            "criterion_matches": context_matches,
            "metrics": {
                "context_precision": context_scores["precision"],
                "context_recall": context_scores["recall"],
            },
        },
        "generation_input": {
            "system_instruction_sha256": sha256(
                SYSTEM_INSTRUCTION.encode("utf-8"),
            ).hexdigest(),
            "prompt": prompt,
            "prompt_tokens_estimate": prompt_tokens_estimate,
        },
    }


def mean_metric(
    cases: list[dict[str, Any]],
    path: tuple[str, ...],
) -> dict[str, Any]:
    """Aggregate only applicable, non-missing values and expose denominators."""

    records = []
    for case in cases:
        value: Any = case
        for key in path:
            value = value[key]
        records.append(value)
    applicable = [item for item in records if item["applicable"]]
    scored = [item["value"] for item in applicable if item["value"] is not None]
    return {
        "value": statistics.fmean(scored) if scored else None,
        "applicable_cases": len(applicable),
        "scored_cases": len(scored),
        "missing_scores": len(applicable) - len(scored),
        "total_cases": len(cases),
    }


def aggregate_offline(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Return overall and language-specific retrieval/context aggregates."""

    paths = {
        "precision_at_5": (
            "retrieval",
            "metrics",
            "precision_at_k",
        ),
        "criterion_recall_at_5": (
            "retrieval",
            "metrics",
            "criterion_recall_at_k",
        ),
        "mrr_at_5": (
            "retrieval",
            "metrics",
            "mrr_at_k",
        ),
        "context_precision": (
            "context",
            "metrics",
            "context_precision",
        ),
        "context_recall": (
            "context",
            "metrics",
            "context_recall",
        ),
    }
    aggregates = {}
    for language in ("all", "en", "sw", "mixed"):
        selected = [
            case
            for case in cases
            if language == "all" or case["language"] == language
        ]
        aggregates[language] = {
            "questions": len(selected),
            **{
                name: mean_metric(selected, path)
                for name, path in paths.items()
            },
            "average_retrieval_ms": (
                statistics.fmean(
                    case["retrieval"]["retrieval_ms"]
                    for case in selected
                )
                if selected
                else None
            ),
            "average_context_build_ms": (
                statistics.fmean(
                    case["context"]["context_build_ms"]
                    for case in selected
                )
                if selected
                else None
            ),
        }
    return aggregates


def validate_dataset(dataset: dict[str, Any], expected_partition: str) -> None:
    """Reject stale or unsafe datasets before any experimental work starts."""

    if dataset.get("schema_version") != "kq-retrieval-evaluation-reviewed-v2":
        raise ValueError("Experimental evaluation requires reviewed schema v2.")
    if dataset.get("partition") != expected_partition:
        raise ValueError("Dataset partition does not match the requested phase.")
    if dataset.get("dataset_status") != "AI-assisted validated and researcher-authorised":
        raise ValueError("Dataset is not researcher-authorised.")
    if not dataset.get("validation", {}).get("valid"):
        raise ValueError("Dataset structural validation did not pass.")
    for case in dataset["cases"]:
        for criterion in case["mandatory_evidence_criteria"]:
            if criterion["status"] != "mapped":
                raise ValueError(
                    f"Unresolved evidence criterion in {case['case_id']}.",
                )


def experiment_configuration(
    dataset_path: Path,
    dataset: dict[str, Any],
    settings: Settings,
    top_k: int,
) -> dict[str, Any]:
    """Record every frozen input needed to reproduce the experiment."""

    configured_models = {
        model.model_id: {
            "provider": model.provider,
            "upstream_model": model.upstream_model,
            "max_output_tokens": model.max_output_tokens,
            "timeout_seconds": model.timeout_seconds,
        }
        for model in settings.generation_models
        if model.model_id in CANDIDATE_MODEL_IDS
    }
    return {
        "phase": "Automated Experimental Evaluation Framework",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_path": str(dataset_path.relative_to(PROJECT_DIR)),
        "dataset_sha256": sha256(dataset_path.read_bytes()).hexdigest(),
        "dataset_partition": dataset["partition"],
        "dataset_status": dataset["dataset_status"],
        "active_index": dataset["active_index"],
        "top_k": top_k,
        "retrieval": {
            "hybrid_enabled": settings.hybrid_retrieval_enabled,
            "dense_candidates": settings.hybrid_dense_candidates,
            "lexical_candidates": settings.hybrid_lexical_candidates,
            "rrf_k": settings.hybrid_rrf_k,
        },
        "context": {
            "min_similarity": settings.context_min_similarity,
            "max_chars": settings.context_max_chars,
            "max_tokens": settings.context_max_tokens,
            "max_documents": settings.context_max_documents,
            "max_chunks_per_document": settings.context_max_chunks_per_document,
        },
        "system_instruction_sha256": sha256(
            SYSTEM_INSTRUCTION.encode("utf-8"),
        ).hexdigest(),
        "candidate_models": configured_models,
    }


def run_offline_development(
    dataset_path: Path,
    retrieval: RetrievalService,
    settings: Settings,
    top_k: int = DEFAULT_TOP_K,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Run development retrieval/context evaluation without provider calls."""

    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    validate_dataset(dataset, "development")
    cases = []
    total = len(dataset["cases"])
    for index, case in enumerate(dataset["cases"], 1):
        result = evaluate_offline_case(
            case,
            retrieval,
            settings,
            top_k,
        )
        cases.append(result)
        if progress:
            progress(index, total, case["case_id"])
    return {
        "schema_version": "kq-automated-experiment-v1",
        "run_status": "offline_development_complete",
        "provider_calls_made": 0,
        "configuration": experiment_configuration(
            dataset_path,
            dataset,
            settings,
            top_k,
        ),
        "metric_formulas": {
            "precision_at_k": "retrieved chunks supporting >=1 criterion / K",
            "criterion_recall_at_k": "mandatory criteria satisfied by any equivalent parent / mandatory criteria",
            "mrr_at_k": "1 / rank of first chunk satisfying any mandatory criterion",
            "context_precision": "selected context chunks supporting >=1 criterion / selected context chunks",
            "context_recall": "mandatory criteria satisfied by actual supplied context / mandatory criteria",
        },
        "aggregates": aggregate_offline(cases),
        "cases": cases,
    }


def export_case_csv(report: dict[str, Any], output_path: Path) -> None:
    """Write a flat case export while the JSON retains complete nested evidence."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "case_id",
        "semantic_group",
        "language",
        "question",
        "reference_answer",
        "context_decision",
        "precision_at_5",
        "criterion_recall_at_5",
        "mrr_at_5",
        "context_precision",
        "context_recall",
        "retrieval_ms",
        "context_build_ms",
        "retrieved_parent_chunk_ids",
        "selected_context_parent_chunk_ids",
    ]
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
        )
        writer.writeheader()
        for case in report["cases"]:
            writer.writerow(
                {
                    "case_id": case["case_id"],
                    "semantic_group": case["semantic_group"],
                    "language": case["language"],
                    "question": case["question"],
                    "reference_answer": case["reference_answer"],
                    "context_decision": case["context"]["decision"],
                    "precision_at_5": case["retrieval"]["metrics"]["precision_at_k"]["value"],
                    "criterion_recall_at_5": case["retrieval"]["metrics"]["criterion_recall_at_k"]["value"],
                    "mrr_at_5": case["retrieval"]["metrics"]["mrr_at_k"]["value"],
                    "context_precision": case["context"]["metrics"]["context_precision"]["value"],
                    "context_recall": case["context"]["metrics"]["context_recall"]["value"],
                    "retrieval_ms": case["retrieval"]["retrieval_ms"],
                    "context_build_ms": case["context"]["context_build_ms"],
                    "retrieved_parent_chunk_ids": "|".join(
                        item["parent_chunk_id"]
                        for item in case["retrieval"]["chunks"]
                    ),
                    "selected_context_parent_chunk_ids": "|".join(
                        case["context"]["selected_parent_chunk_ids"],
                    ),
                },
            )


def validate_models(generation: GenerationService) -> dict[str, Any]:
    """Ensure live comparison uses exactly the three requested model versions."""

    configured = {
        model.model_id: model
        for model in generation.configured_models()
    }
    errors = []
    for model_id in CANDIDATE_MODEL_IDS:
        model = configured.get(model_id)
        if model is None:
            errors.append(f"Missing configured model: {model_id}")
            continue
        if model.upstream_model != EXPECTED_UPSTREAM_MODELS[model_id]:
            errors.append(f"Unexpected upstream model for {model_id}.")
        if not model.configured:
            errors.append(f"Missing API credential for {model_id}.")
    if errors:
        raise ValueError(" ".join(errors))
    return {
        model_id: configured[model_id].model_dump(mode="json")
        for model_id in CANDIDATE_MODEL_IDS
    }


def run_generation_calls(
    offline_report: dict[str, Any],
    generation: GenerationService,
    checkpoint_path: Path,
    *,
    authorised: bool,
    progress: Callable[[int, int, str], None] | None = None,
) -> list[dict[str, Any]]:
    """Generate model answers with an explicit authorization gate and resume."""

    if not authorised:
        raise PermissionError(
            "Live model calls require explicit authorization.",
        )
    validate_models(generation)
    previous = []
    if checkpoint_path.exists():
        previous = json.loads(
            checkpoint_path.read_text(encoding="utf-8"),
        ).get("outcomes", [])
    completed = {
        (item["case_id"], item["model_id"]): item
        for item in previous
        if item.get("status") == "completed"
    }
    outcomes = list(completed.values())
    work = [
        (case, model_id)
        for case in offline_report["cases"]
        for model_id in CANDIDATE_MODEL_IDS
        if (case["case_id"], model_id) not in completed
    ]
    total = len(completed) + len(work)
    for case, model_id in work:
        prompt = case["generation_input"]["prompt"]
        started = perf_counter()
        if not prompt:
            outcome = {
                "case_id": case["case_id"],
                "model_id": model_id,
                "language": case["language"],
                "status": "not_called",
                "response": case["context"]["message"],
                "failure": None,
                "failure_type": None,
                "generation_called": False,
                "response_time_ms": 0.0,
                "metric_applicability": {
                    "faithfulness": "not_applicable_no_generated_response",
                    "answer_relevance": "applicable_if_response_present",
                },
            }
        else:
            try:
                response = generation.generate(
                    prompt,
                    model_id=model_id,
                    system_instruction=SYSTEM_INSTRUCTION,
                )
                outcome = {
                    "case_id": case["case_id"],
                    "model_id": model_id,
                    "language": case["language"],
                    "status": "completed",
                    "response": response.text,
                    "failure": None,
                    "failure_type": None,
                    "generation_called": True,
                    "provider_request_id": response.provider_request_id,
                    "provider_duration_ms": response.provider_duration_ms,
                    "attempt_count": response.attempt_count,
                    "response_time_ms": round(
                        (perf_counter() - started) * 1000,
                        3,
                    ),
                    "metric_applicability": {
                        "faithfulness": "applicable",
                        "answer_relevance": "applicable",
                    },
                }
            except GenerationProviderError as error:
                outcome = {
                    "case_id": case["case_id"],
                    "model_id": model_id,
                    "language": case["language"],
                    "status": "failed",
                    "response": None,
                    "failure": error.detail,
                    "failure_type": f"provider_{error.status_code}",
                    "generation_called": True,
                    "response_time_ms": round(
                        (perf_counter() - started) * 1000,
                        3,
                    ),
                    "metric_applicability": {
                        "faithfulness": "missing_generation_failure",
                        "answer_relevance": "missing_generation_failure",
                    },
                }
        outcomes.append(outcome)
        write_json_atomic(
            checkpoint_path,
            {
                "source_run_status": offline_report["run_status"],
                "outcomes": outcomes,
            },
        )
        if progress:
            progress(
                len(outcomes),
                total,
                f"{case['case_id']} / {model_id}",
            )
    return outcomes


def expected_response_class(expected_behavior: str) -> str:
    """Reduce reviewed behavior labels to classes used by fallback scoring."""

    return FALLBACK_EXPECTATIONS.get(expected_behavior, "answer")


def score_fallback_accuracy(
    case: dict[str, Any],
    outcome: dict[str, Any],
) -> dict[str, Any]:
    """Score deterministic fallbacks and leave generated intent judgments open.

    A generated answer can only be classified as an answer, fallback, or
    clarification by an independent judge. Provider failures are never treated
    as fallbacks, and therefore do not receive a fallback-accuracy score.
    """

    expected = expected_response_class(case["expected_behavior"])
    if outcome["status"] == "failed":
        return metric(
            value=None,
            applicable=False,
            numerator=None,
            denominator=None,
            method="expected_vs_observed_response_class",
            missing_reason="Technical generation failure is not a fallback.",
        )
    if outcome["generation_called"]:
        return metric(
            value=None,
            applicable=True,
            numerator=None,
            denominator=1,
            method="expected_vs_judge_classified_response_class",
            missing_reason=(
                "Generated response class requires an independent judgment."
            ),
        )

    observed = (
        "clarification"
        if case["context"]["decision"] == "clarification_required"
        else "fallback"
    )
    correct = observed == expected or (
        expected == "fallback_or_clarification"
        and observed in {"fallback", "clarification"}
    )
    return metric(
        value=float(correct),
        applicable=True,
        numerator=int(correct),
        denominator=1,
        method="expected_vs_deterministic_pipeline_response_class",
    )


def aggregate_generation(
    offline_report: dict[str, Any],
    outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate latency, failures, and fallback accuracy by model/language."""

    case_by_id = {
        case["case_id"]: case
        for case in offline_report["cases"]
    }
    enriched = []
    for outcome in outcomes:
        case = case_by_id[outcome["case_id"]]
        enriched.append(
            {
                **outcome,
                "fallback_accuracy": score_fallback_accuracy(case, outcome),
            },
        )

    aggregates: dict[str, Any] = {}
    for model_id in CANDIDATE_MODEL_IDS:
        model_rows = [row for row in enriched if row["model_id"] == model_id]
        scopes = {
            "all": model_rows,
            **{
                language: [
                    row for row in model_rows if row["language"] == language
                ]
                for language in ("en", "sw", "mixed")
            },
        }
        aggregates[model_id] = {}
        for scope, rows in scopes.items():
            called = [row for row in rows if row["generation_called"]]
            failed = [row for row in called if row["status"] == "failed"]
            latency = [
                row["response_time_ms"]
                for row in called
                if row["status"] == "completed"
            ]
            fallback_records = [
                row["fallback_accuracy"]
                for row in rows
                if row["fallback_accuracy"]["applicable"]
            ]
            fallback_scores = [
                record["value"]
                for record in fallback_records
                if record["value"] is not None
            ]
            aggregates[model_id][scope] = {
                "outcomes": len(rows),
                "generation_calls": len(called),
                "failure_rate": metric(
                    value=(len(failed) / len(called) if called else None),
                    applicable=bool(called),
                    numerator=len(failed),
                    denominator=len(called),
                    method="technical_generation_failures_per_provider_call",
                    missing_reason=(None if called else "No provider calls."),
                ),
                "average_response_time_ms": (
                    statistics.fmean(latency) if latency else None
                ),
                "fallback_accuracy": {
                    "value": (
                        statistics.fmean(fallback_scores)
                        if fallback_scores
                        else None
                    ),
                    "applicable_cases": len(fallback_records),
                    "scored_cases": len(fallback_scores),
                    "missing_scores": (
                        len(fallback_records) - len(fallback_scores)
                    ),
                },
            }
    return {
        "outcomes": enriched,
        "aggregates": aggregates,
    }


def estimated_api_usage(report: dict[str, Any]) -> dict[str, Any]:
    """Estimate calls and tokens without invoking a provider tokenizer or API."""

    generation_cases = [
        case
        for case in report["cases"]
        if case["generation_input"]["prompt"]
    ]
    prompt_tokens_per_model = sum(
        case["generation_input"]["prompt_tokens_estimate"]
        for case in generation_cases
    )
    output_limit = max(
        (
            model["max_output_tokens"]
            for model in report["configuration"]["candidate_models"].values()
        ),
        default=1024,
    )
    generation_calls = len(generation_cases) * len(CANDIDATE_MODEL_IDS)
    return {
        "generation_cases_per_model": len(generation_cases),
        "generation_calls_all_models": generation_calls,
        "estimated_prompt_tokens_all_models": (
            prompt_tokens_per_model * len(CANDIDATE_MODEL_IDS)
        ),
        "maximum_output_tokens_all_models": generation_calls * output_limit,
        "estimation_note": (
            "Prompt tokens use the existing provider-neutral four-character "
            "estimate; output tokens are a configured upper bound."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run development-only offline criterion-aware evaluation.",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DEVELOPMENT_DATASET,
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        choices=range(1, 21),
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--csv-output",
        type=Path,
        required=True,
    )
    args = parser.parse_args(argv)

    def show_progress(completed: int, total: int, identity: str) -> None:
        print(
            f"[{completed}/{total}] {identity}",
            flush=True,
        )

    report = run_offline_development(
        args.dataset,
        get_retrieval_service(),
        get_settings(),
        args.top_k,
        progress=show_progress,
    )
    report["estimated_api_usage"] = estimated_api_usage(report)
    write_json_atomic(
        args.output,
        report,
    )
    export_case_csv(
        report,
        args.csv_output,
    )
    print(
        json.dumps(
            report["aggregates"],
            ensure_ascii=False,
            indent=2,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
