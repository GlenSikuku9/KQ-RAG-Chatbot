"""Official RAGAS 0.4 evaluation orchestration for offline reports only.

Passenger requests never import or call this module. The CLI supplies exact
generation contexts and responses, while Firestore receives scores and
non-sensitive provenance rather than questions, answers, or policy text.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from importlib.metadata import version
from typing import Any
import re

from app.config import Settings
from app.rag.embeddings import get_embedder


RAGAS_METRICS = (
    "context_precision",
    "context_recall",
    "faithfulness",
    "answer_relevance",
)


class LocalE5RagasEmbedding:
    """Adapt the existing normalized multilingual E5 query embedding to RAGAS."""

    def __new__(cls):
        # Import lazily so the production API does not need RAGAS at startup.
        from ragas.embeddings.base import BaseRagasEmbedding

        class Adapter(BaseRagasEmbedding):
            def __init__(self):
                super().__init__()
                self._embedder = get_embedder()

            def embed_text(self, text: str, **kwargs: Any) -> list[float]:
                return self._embedder.embed_query(text)

            async def aembed_text(
                self,
                text: str,
                **kwargs: Any,
            ) -> list[float]:
                return await asyncio.to_thread(self.embed_text, text)

        return Adapter()


def build_ragas_scorers(settings: Settings) -> tuple[dict[str, Any], dict]:
    """Build current collections-API metrics from explicit evaluator settings."""

    if not settings.ragas_evaluator_model:
        raise ValueError("Set RAGAS_EVALUATOR_MODEL before running evaluation.")
    if not settings.ragas_evaluator_api_key:
        raise ValueError("Set RAGAS_EVALUATOR_API_KEY before running evaluation.")

    from openai import AsyncOpenAI
    from ragas.llms import llm_factory
    from ragas.metrics.collections import (
        AnswerRelevancy,
        ContextPrecision,
        ContextRecall,
        Faithfulness,
    )

    client = AsyncOpenAI(
        api_key=settings.ragas_evaluator_api_key,
        base_url=settings.ragas_evaluator_base_url,
        timeout=settings.ragas_evaluator_timeout_seconds,
        max_retries=0,
    )
    llm = llm_factory(
        settings.ragas_evaluator_model,
        provider="openai",
        client=client,
        temperature=0,
    )
    embeddings = LocalE5RagasEmbedding()
    scorers = {
        "context_precision": ContextPrecision(llm=llm),
        "context_recall": ContextRecall(llm=llm),
        "faithfulness": Faithfulness(llm=llm),
        "answer_relevance": AnswerRelevancy(
            llm=llm,
            embeddings=embeddings,
        ),
    }
    metadata = {
        "ragas_version": version("ragas"),
        "evaluator_model": settings.ragas_evaluator_model,
        "evaluator_base_url": settings.ragas_evaluator_base_url,
        "embedding_method": "local_multilingual_e5_query_embeddings",
        "evaluation_method": "ragas_collections_api_v0.4",
    }
    return scorers, metadata


async def _score(
    scorer: Any,
    metric_name: str,
    values: dict,
    max_rate_limit_retries: int = 6,
) -> dict:
    """Return a score after bounded, provider-directed rate-limit retries."""

    for attempt in range(max_rate_limit_retries + 1):
        try:
            result = await scorer.ascore(**values)
            score = float(result.value)
            return {
                "metric_name": metric_name,
                "score": score,
                "status": "evaluated",
                "reason": getattr(result, "reason", None),
                "attempts": attempt + 1,
            }
        except Exception as exc:  # Provider and parsing failures stay explicit.
            recoverable = type(exc).__name__ in {
                "RateLimitError",
                "APIConnectionError",
                "APITimeoutError",
                "InternalServerError",
            }
            if recoverable and attempt < max_rate_limit_retries:
                await asyncio.sleep(_retry_wait_seconds(exc, attempt))
                continue
            return {
                "metric_name": metric_name,
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "attempts": attempt + 1,
            }


def _retry_wait_seconds(error: Exception, attempt: int) -> float:
    """Use provider-directed delays and bounded backoff for transient errors."""

    message = str(error)
    seconds_match = re.search(
        r"try again in\s+([0-9.]+)s",
        message,
        flags=re.IGNORECASE,
    )
    if seconds_match:
        return min(60.0, float(seconds_match.group(1)) + 0.75)
    milliseconds_match = re.search(
        r"try again in\s+([0-9.]+)ms",
        message,
        flags=re.IGNORECASE,
    )
    if milliseconds_match:
        return min(
            60.0,
            float(milliseconds_match.group(1)) / 1000 + 0.75,
        )
    return min(60.0, 2.0 ** (attempt + 1))


async def evaluate_report(
    report: dict,
    scorers: dict[str, Any],
    metadata: dict,
    prior_results: list[dict] | None = None,
    progress_callback: Any | None = None,
) -> dict:
    """Evaluate shared contexts once and model responses independently."""

    evaluated_at = datetime.now(timezone.utc)
    context_by_case = {
        item["case_id"]: item
        for item in report.get("context_outcomes", [])
    }
    # Only successful judgments are reusable. Failed attempts are retried, and
    # the context fingerprint prevents reuse against different evidence.
    reusable = {
        _result_key(item): item
        for item in (prior_results or [])
        if item.get("status") == "evaluated"
    }
    results: list[dict] = list(reusable.values())
    total = _expected_judgment_count(report, context_by_case)
    completed = len(results)

    for context in context_by_case.values():
        if not context.get("context_metric_eligible"):
            continue
        if context.get("ragas_reference_status") != "validated":
            continue
        inputs = {
            "user_input": context["question"],
            "reference": context["reference_answer"],
            "retrieved_contexts": context.get("selected_contexts", []),
        }
        for metric_name in ("context_precision", "context_recall"):
            key = (
                metric_name,
                context["case_id"],
                None,
                context["context_fingerprint"],
            )
            if key in reusable:
                continue
            result = await _score(
                scorers[metric_name],
                metric_name,
                inputs,
            )
            results.append({
                **result,
                "case_id": context["case_id"],
                "language": context["language"],
                "model_id": None,
                "context_fingerprint": context["context_fingerprint"],
            })
            completed += 1
            if progress_callback:
                progress_callback(results[-1], completed, total, results)

    for outcome in report.get("outcomes", []):
        context = context_by_case.get(outcome.get("case_id"))
        if not context or not context.get("context_metric_eligible"):
            continue
        if outcome.get("failure") or not outcome.get("generation_called"):
            continue
        common = {
            "user_input": context["question"],
            "response": outcome["answer"],
        }
        metric_inputs = {
            "faithfulness": {
                **common,
                "retrieved_contexts": context.get(
                    "selected_contexts",
                    [],
                ),
            },
            "answer_relevance": common,
        }
        for metric_name, inputs in metric_inputs.items():
            key = (
                metric_name,
                context["case_id"],
                outcome["model_id"],
                context["context_fingerprint"],
            )
            if key in reusable:
                continue
            result = await _score(
                scorers[metric_name],
                metric_name,
                inputs,
            )
            results.append({
                **result,
                "case_id": context["case_id"],
                "language": context["language"],
                "model_id": outcome["model_id"],
                "context_fingerprint": context["context_fingerprint"],
            })
            completed += 1
            if progress_callback:
                progress_callback(results[-1], completed, total, results)

    run_id = f"{report['run_id']}-ragas"
    aggregates = aggregate_results(results, run_id, evaluated_at, metadata)
    failure_groups: dict[str, dict] = {}
    for item in results:
        if item["status"] == "failed":
            group = failure_groups.setdefault(
                item["error_type"],
                {
                    "error_type": item["error_type"],
                    "error": item["error"],
                    "count": 0,
                },
            )
            group["count"] += 1
    return {
        "run_id": run_id,
        "source_run_id": report["run_id"],
        "evaluated_at": evaluated_at.isoformat(),
        **metadata,
        "results": results,
        "failure_summary": list(failure_groups.values()),
        "aggregates": [
            {
                **item,
                "evaluated_at": item["evaluated_at"].isoformat(),
            }
            for item in aggregates
        ],
    }


def _result_key(item: dict) -> tuple:
    """Identify one judgment independently of retry or output ordering."""

    return (
        item.get("metric_name"),
        item.get("case_id"),
        item.get("model_id"),
        item.get("context_fingerprint"),
    )


def _expected_judgment_count(report: dict, context_by_case: dict) -> int:
    """Count eligible context and response judgments for progress reporting."""

    context_count = sum(
        2
        for context in context_by_case.values()
        if context.get("context_metric_eligible")
        and context.get("ragas_reference_status") == "validated"
    )
    response_count = sum(
        2
        for outcome in report.get("outcomes", [])
        if (
            (context := context_by_case.get(outcome.get("case_id")))
            and context.get("context_metric_eligible")
            and not outcome.get("failure")
            and outcome.get("generation_called")
        )
    )
    return context_count + response_count


def aggregate_results(
    results: list[dict],
    run_id: str,
    evaluated_at: datetime,
    metadata: dict,
) -> list[dict]:
    """Aggregate successful judgments without duplicating shared retrieval."""

    successful = [item for item in results if item["status"] == "evaluated"]
    records = []
    models = sorted({item["model_id"] for item in successful if item["model_id"]})
    languages = ("all", "en", "sw", "mixed")
    for metric_name in RAGAS_METRICS:
        metric_items = [
            item for item in successful if item["metric_name"] == metric_name
        ]
        model_options = [None] if metric_name.startswith("context_") else [None, *models]
        for model_id in model_options:
            for language in languages:
                selected = [
                    item
                    for item in metric_items
                    if (model_id is None or item["model_id"] == model_id)
                    and (language == "all" or item["language"] == language)
                ]
                if not selected:
                    continue
                records.append({
                    "record_type": "aggregate",
                    "run_id": run_id,
                    "evaluated_at": evaluated_at,
                    "metric_name": metric_name,
                    "model_id": model_id,
                    "language": language,
                    "score": round(
                        sum(item["score"] for item in selected) / len(selected),
                        6,
                    ),
                    "sample_size": len(selected),
                    "status": "evaluated",
                    **metadata,
                })
    return records

