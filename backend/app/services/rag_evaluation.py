"""Read persisted offline RAG evaluations and aggregate live RAG operations.

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


def _weighted(
    records: list[dict],
    field: str,
    count_field: str = "sample_size",
) -> tuple[float | None, int]:
    """Combine language records using their evaluated enquiry counts."""

    usable = [
        item
        for item in records
        if item.get(field) is not None
        and int(item.get(count_field, item.get("sample_size", 0))) > 0
    ]
    count = sum(
        int(item.get(count_field, item.get("sample_size", 0)))
        for item in usable
    )
    if not count:
        return None, 0
    value = sum(
        float(item[field])
        * int(item.get(count_field, item.get("sample_size", 0)))
        for item in usable
    ) / count
    return round(value, 6), count


def aggregate_rag_operations(
    events: list[OperationalEvent],
    model_id: str | None = None,
) -> dict[str, RAGMetricValue]:
    """Calculate latency and failure rate from RAG-only operational events."""

    selected = [
        event
        for event in events
        if event.event_type in {"chat_completed", "chat_failed"}
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
        retrieval_snapshots = list(
            client.collection("retrieval_evaluations").stream(
                timeout=FIRESTORE_READ_TIMEOUT,
            )
        )
        model_snapshots = list(
            client.collection("model_evaluations").stream(
                timeout=FIRESTORE_READ_TIMEOUT,
            )
        )
        context_snapshots = list(
            client.collection("context_evaluations").stream(
                timeout=FIRESTORE_READ_TIMEOUT,
            )
        )
        ragas_snapshots = list(
            client.collection("ragas_evaluations").stream(
                timeout=FIRESTORE_READ_TIMEOUT,
            )
        )
        event_snapshots = list(
            client.collection("operational_events").stream(
                timeout=FIRESTORE_READ_TIMEOUT,
            )
        )

    retrieval_records = _records_in_period(retrieval_snapshots, started)
    model_records = _records_in_period(model_snapshots, started)
    context_records = _records_in_period(context_snapshots, started)
    ragas_records = _records_in_period(ragas_snapshots, started)
    available_models = sorted(
        {
            str(item["model_id"])
            for item in model_records + ragas_records
            if item.get("model_id")
        }
    )
    available_languages = sorted(
        {
            str(item["language"])
            for item in (
                retrieval_records
                + model_records
                + context_records
                + ragas_records
            )
            if item.get("language") and item.get("language") != "all"
        }
    )

    retrieval = _latest_retrieval(retrieval_records, language)
    response_records = _latest_model_run(model_records, model_id, language)
    context_record = _latest_context(context_records, language)
    ragas = _latest_ragas_run(ragas_records, model_id, language)
    evaluated_times = [
        item.get("evaluated_at")
        for item in (
            ([retrieval] if retrieval else [])
            + ([context_record] if context_record else [])
            + response_records
            + ragas
        )
        if isinstance(item.get("evaluated_at"), datetime)
    ]

    events = []
    for snapshot in event_snapshots:
        event = OperationalEvent.model_validate(snapshot.to_dict())
        if event.occurred_at >= started:
            events.append(event)

    retrieval_count = int((retrieval or {}).get("sample_size", 0))
    if context_record:
        context_precision = context_record.get("context_precision")
        context_recall = context_record.get("context_recall")
        fallback_accuracy = context_record.get("fallback_accuracy")
        context_precision_count = int(
            context_record.get("context_sample_size", 0),
        )
        context_recall_count = context_precision_count
        fallback_count = int(
            context_record.get("fallback_sample_size", 0),
        )
    else:
        # Compatibility for runs persisted before unique context records were
        # introduced. Deduplicate identical model-independent language rows.
        unique_context = _unique_context_records(response_records)
        context_precision, context_precision_count = _weighted(
            unique_context,
            "context_precision",
            "context_sample_size",
        )
        context_recall, context_recall_count = _weighted(
            unique_context,
            "context_recall",
            "context_sample_size",
        )
        fallback_accuracy, fallback_count = _weighted(
            unique_context,
            "fallback_accuracy",
            "fallback_sample_size",
        )

    document_metrics = {
        "document_source_precision": _metric(
            context_precision,
            context_precision_count,
            "custom_document_source_overlap_unique_questions",
        ),
        "document_source_recall": _metric(
            context_recall,
            context_recall_count,
            "custom_document_source_overlap_unique_questions",
        ),
    }
    ragas_metrics = {}
    for metric_name in (
        "context_precision",
        "context_recall",
        "faithfulness",
        "answer_relevance",
    ):
        record = next(
            (item for item in ragas if item.get("metric_name") == metric_name),
            None,
        )
        ragas_metrics[metric_name] = _metric(
            (record or {}).get("score"),
            int((record or {}).get("sample_size", 0)),
            (record or {}).get(
                "evaluation_method",
                "requires_explicit_ragas_evaluation",
            ),
        )

    return RAGEvaluationMetrics(
        period_days=period_days,
        model_id=model_id,
        language=language,
        retrieval_k=(retrieval or {}).get("top_k"),
        evaluated_at=max(evaluated_times) if evaluated_times else None,
        available_models=available_models,
        available_languages=available_languages,
        retrieval={
            "precision_at_k": _metric(
                (retrieval or {}).get("precision_at_k"),
                retrieval_count,
                "labelled_relevant_chunks",
            ),
            "recall_at_k": _metric(
                (retrieval or {}).get("recall_at_k"),
                retrieval_count,
                "labelled_relevant_chunks",
            ),
            "mrr_at_k": _metric(
                (retrieval or {}).get("mrr_at_k"),
                retrieval_count,
                "first_labelled_relevant_chunk_rank",
            ),
        },
        document_retrieval=document_metrics,
        ragas=ragas_metrics,
        response_quality={
            # Compatibility keys keep older clients working without presenting
            # these custom values as official RAGAS context metrics.
            "context_precision": document_metrics[
                "document_source_precision"
            ],
            "context_recall": document_metrics["document_source_recall"],
            "faithfulness": ragas_metrics["faithfulness"],
            "answer_relevance": ragas_metrics["answer_relevance"],
        },
        fallback={
            "fallback_accuracy": _metric(
                fallback_accuracy,
                fallback_count,
                "labelled_supported_and_unsupported_enquiries",
            ),
        },
        operational=aggregate_rag_operations(events, model_id),
    )


def _latest_retrieval(records: list[dict], language: str) -> dict | None:
    matches = [item for item in records if item.get("language") == language]
    return max(
        matches,
        key=lambda item: item.get("evaluated_at")
        or datetime.min.replace(tzinfo=timezone.utc),
        default=None,
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


def _unique_context_records(records: list[dict]) -> list[dict]:
    """Avoid tripling model-independent context results across AI models."""

    unique = {}
    for item in records:
        key = (
            item.get("run_id"),
            item.get("language"),
            item.get("dataset_version"),
            item.get("knowledge_base_version"),
        )
        unique.setdefault(key, item)
    return list(unique.values())


def _records_in_period(snapshots: list, started: datetime) -> list[dict]:
    """Ignore malformed and out-of-period evaluation records safely."""

    records = []
    for snapshot in snapshots:
        record = snapshot.to_dict() or {}
        evaluated_at = record.get("evaluated_at")
        if isinstance(evaluated_at, datetime) and evaluated_at >= started:
            records.append(record)
    return records


def _latest_model_run(
    records: list[dict],
    model_id: str | None,
    language: str,
) -> list[dict]:
    matches = [
        item
        for item in records
        if model_id is None or item.get("model_id") == model_id
    ]
    if language != "all":
        matches = [item for item in matches if item.get("language") == language]
    if not matches:
        return []
    newest = max(
        matches,
        key=lambda item: item.get("evaluated_at")
        or datetime.min.replace(tzinfo=timezone.utc),
    )
    run_id = newest.get("run_id")
    return [item for item in matches if item.get("run_id") == run_id]


def _latest_ragas_run(
    records: list[dict],
    model_id: str | None,
    language: str,
) -> list[dict]:
    """Select one RAGAS run while retaining model-independent context rows."""

    aggregates = [
        item
        for item in records
        if item.get("record_type") == "aggregate"
        and item.get("language") == language
        and (
            item.get("metric_name") in {"context_precision", "context_recall"}
            or item.get("model_id") == model_id
        )
    ]
    if not aggregates:
        return []
    newest = max(
        aggregates,
        key=lambda item: item.get("evaluated_at")
        or datetime.min.replace(tzinfo=timezone.utc),
    )
    return [
        item for item in aggregates if item.get("run_id") == newest.get("run_id")
    ]
