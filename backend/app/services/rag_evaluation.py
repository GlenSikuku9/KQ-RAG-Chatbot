"""Read fallback evaluations and aggregate live RAG operations.

Offline evaluation is intentionally separate from passenger requests. This
module never calls a generator, retrieval model, or external evaluation API.
"""

from datetime import datetime, timedelta, timezone
from math import ceil

from app.models.admin import RAGEvaluationMetrics, RAGMetricValue
from app.models.database import utc_now
from app.models.feedback import OperationalEvent
from app.services.firestore_client import (
    FIRESTORE_READ_TIMEOUT,
    firestore_operation,
    get_firestore_client,
)


def is_retired_analytics_model(event: OperationalEvent) -> bool:
    """Exclude historical Gemini events from current analytics."""

    identifiers = " ".join(
        value.lower()
        for value in (
            event.model_id,
            event.model_provider,
            event.model_version,
        )
        if value
    )
    return "gemini" in identifiers or "google_ai_studio" in identifiers


def _metric(
    value: float | None,
    sample_size: int,
    method: str,
) -> RAGMetricValue:
    """Never turn unavailable evaluation evidence into a misleading zero."""

    if value is None:
        status = "not_evaluated" if sample_size == 0 else "insufficient_data"
    else:
        status = "evaluated"
    return RAGMetricValue(
        value=value,
        sample_size=sample_size,
        status=status,
        method=method,
    )


def aggregate_rag_operations(
    events: list[OperationalEvent],
    model_id: str | None = None,
) -> dict[str, RAGMetricValue]:
    """Calculate latency and failure rate from RAG-only operational events."""

    selected = [
        event
        for event in events
        if not is_retired_analytics_model(event)
        and event.event_type in {"chat_completed", "chat_failed"}
        and event.response_source not in {
            "flight_information",
            "non_rag",
            "unsupported",
        }
        and event.external_provider != "OAG"
        and (model_id is None or event.model_id == model_id)
    ]
    completed = [
        event
        for event in selected
        if event.event_type == "chat_completed"
        and event.duration_ms is not None
    ]
    durations = sorted(float(event.duration_ms) for event in completed)
    attempts = len(selected)
    failures = sum(event.event_type == "chat_failed" for event in selected)
    average_ms = sum(durations) / len(durations) if durations else None
    failure_rate = failures / attempts if attempts else None

    return {
        "average_response_ms": _metric(
            round(average_ms, 2) if average_ms is not None else None,
            len(durations),
            "operational_events_rag_only",
        ),
        "failure_rate": _metric(
            round(failure_rate, 6) if failure_rate is not None else None,
            attempts,
            "failed_rag_attempts_divided_by_all_rag_attempts",
        ),
        "median_response_ms": _metric(
            _percentile(durations, 0.5),
            len(durations),
            "operational_events_rag_only",
        ),
        "p95_response_ms": _metric(
            _percentile(durations, 0.95),
            len(durations),
            "operational_events_rag_only",
        ),
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    """Return the nearest-rank percentile used by the operational summary."""

    if not values:
        return None
    index = max(0, ceil(percentile * len(values)) - 1)
    return round(values[index], 2)


def evaluation_metrics(
    period_days: int,
    model_id: str | None = None,
    language: str = "all",
) -> RAGEvaluationMetrics:
    """Return the newest compatible offline run plus period-bound operations."""

    started = utc_now() - timedelta(days=period_days)
    client = get_firestore_client()
    with firestore_operation():
        context_snapshots = list(
            client.collection("context_evaluations").stream(
                timeout=FIRESTORE_READ_TIMEOUT,
            )
        )
        event_snapshots = list(
            client.collection("operational_events").stream(
                timeout=FIRESTORE_READ_TIMEOUT,
            )
        )

    context_records = _records_in_period(context_snapshots, started)
    available_languages = sorted(
        {
            str(item["language"])
            for item in context_records
            if item.get("language") and item.get("language") != "all"
        }
    )

    context_record = _latest_context(context_records, language)
    evaluated_times = [
        item.get("evaluated_at")
        for item in ([context_record] if context_record else [])
        if isinstance(item.get("evaluated_at"), datetime)
    ]

    events = []
    for snapshot in event_snapshots:
        event = OperationalEvent.model_validate(snapshot.to_dict())
        if (
            event.occurred_at >= started
            and not is_retired_analytics_model(event)
        ):
            events.append(event)
    available_models = sorted(
        {str(event.model_id) for event in events if event.model_id}
    )

    if context_record:
        fallback_accuracy = context_record.get("fallback_accuracy")
        fallback_count = int(
            context_record.get("fallback_sample_size", 0),
        )
    else:
        fallback_accuracy, fallback_count = None, 0

    return RAGEvaluationMetrics(
        period_days=period_days,
        model_id=model_id,
        language=language,
        evaluated_at=max(evaluated_times) if evaluated_times else None,
        available_models=available_models,
        available_languages=available_languages,
        fallback={
            "fallback_accuracy": _metric(
                fallback_accuracy,
                fallback_count,
                "labelled_supported_and_unsupported_enquiries",
            ),
        },
        operational=aggregate_rag_operations(events, model_id),
    )


def _latest_context(records: list[dict], language: str) -> dict | None:
    """Return the newest unique-question context aggregate for a language."""

    matches = [item for item in records if item.get("language") == language]
    return max(
        matches,
        key=lambda item: item.get("evaluated_at")
        or datetime.min.replace(tzinfo=timezone.utc),
        default=None,
    )


def _records_in_period(snapshots: list, started: datetime) -> list[dict]:
    """Ignore malformed and out-of-period evaluation records safely."""

    records = []
    for snapshot in snapshots:
        record = snapshot.to_dict() or {}
        evaluated_at = record.get("evaluated_at")
        if isinstance(evaluated_at, datetime) and evaluated_at >= started:
            records.append(record)
    return records


