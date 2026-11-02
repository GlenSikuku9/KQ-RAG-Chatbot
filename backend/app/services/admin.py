"""Administrator services. Routes authorize first; this module never trusts client roles."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from firebase_admin import auth

from app.config import get_settings
from app.models.admin import DEFAULT_PASSENGER_THEME, AdminAnalytics, AdminAuditEvent, AdminConversationSummary, AdminDocument, AdminLog, AdminModel, AdminUser, EvaluationResult, PassengerTheme
from app.models.generation import ConfiguredModel
from app.models.feedback import Feedback, OperationalEvent
from app.models.database import encode_document_id, utc_now
from app.models.ingestion import DocumentRecord
from app.models.user import AuthenticatedUser
from app.services.conversations import ConversationStore
from app.services.firestore_client import FIRESTORE_READ_TIMEOUT, firestore_operation, get_firestore_client
from app.services.knowledge_base import KnowledgeBaseStore
from app.rag.ingestion import publish_document, remove_document


def _to_datetime(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, timezone.utc) if value else None


def audit(admin: AuthenticatedUser, action: str, target_type: str, target_id: str) -> None:
    """Write a minimal audit record without credentials or customer message content."""
    event = AdminAuditEvent(
        activity_id=uuid4().hex,
        admin_id=admin.uid,
        action=action,
        target_type=target_type,
        target_id=target_id,
        timestamp=utc_now(),
    )
    with firestore_operation():
        get_firestore_client().collection("admin_activity").document(event.activity_id).set(event.model_dump())


def get_passenger_theme() -> PassengerTheme:
    with firestore_operation():
        snapshot = get_firestore_client().collection("app_settings").document("passenger_theme").get()
    if not snapshot.exists:
        return PassengerTheme.model_validate(DEFAULT_PASSENGER_THEME)
    return PassengerTheme.model_validate(snapshot.to_dict())


def save_passenger_theme(admin: AuthenticatedUser, theme: PassengerTheme) -> PassengerTheme:
    with firestore_operation():
        get_firestore_client().collection("app_settings").document("passenger_theme").set(theme.model_dump())
    audit(admin, "passenger_theme_updated", "settings", "passenger_theme")
    return theme


def list_admin_models() -> list[AdminModel]:
    """Combine configured model metadata with real operational events; credentials never leave config."""
    from app.rag.generation import get_generation_service
    configured = get_generation_service().configured_models()
    with firestore_operation():
        snapshots = list(get_firestore_client().collection("operational_events").stream(timeout=FIRESTORE_READ_TIMEOUT))
    stats = {item.model_id: {"success": 0, "failed": 0, "durations": []} for item in configured}
    for snapshot in snapshots:
        raw = snapshot.to_dict() or {}
        model_id = raw.get("model_id")
        if model_id not in stats:
            continue
        if raw.get("event_type") == "chat_completed":
            stats[model_id]["success"] += 1
            stats[model_id]["durations"].append(float(raw.get("duration_ms", 0)))
        elif raw.get("event_type") == "chat_failed":
            stats[model_id]["failed"] += 1
    total = sum(item["success"] for item in stats.values())
    return [AdminModel(
        model_id=item.model_id, provider=item.provider, upstream_model=item.upstream_model,
        language_support=["en", "sw", "mixed"],
        configured=item.configured, is_default=item.is_default,
        usage_count=stats[item.model_id]["success"],
        usage_percentage=round(stats[item.model_id]["success"] / total * 100, 2) if total else 0,
        average_response_ms=round(sum(stats[item.model_id]["durations"]) / len(stats[item.model_id]["durations"]), 2) if stats[item.model_id]["durations"] else None,
        successful_requests=stats[item.model_id]["success"], failed_requests=stats[item.model_id]["failed"],
    ) for item in configured]


def set_default_model(admin: AuthenticatedUser, model_id: str) -> AdminModel:
    if not any(item.model_id == model_id for item in list_admin_models()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The selected model is not configured.")
    with firestore_operation():
        get_firestore_client().collection("app_settings").document("generation_config").set({"default_model_id": model_id}, merge=True)
    audit(admin, "default_model_updated", "model", model_id)
    return next(item for item in list_admin_models() if item.model_id == model_id)


def list_evaluations() -> list[EvaluationResult]:
    with firestore_operation():
        snapshots = get_firestore_client().collection("model_evaluations").stream(timeout=FIRESTORE_READ_TIMEOUT)
    return sorted((EvaluationResult.model_validate(snapshot.to_dict()) for snapshot in snapshots), key=lambda item: item.evaluated_at, reverse=True)


def list_logs() -> list[AdminLog]:
    """Expose safe operational/audit summaries, never raw credentials or customer content."""
    client = get_firestore_client()
    with firestore_operation():
        audit_snapshots = list(client.collection("admin_activity").stream(timeout=FIRESTORE_READ_TIMEOUT))
        event_snapshots = list(client.collection("operational_events").stream(timeout=FIRESTORE_READ_TIMEOUT))
    logs: list[AdminLog] = []
    for snapshot in audit_snapshots:
        item = snapshot.to_dict() or {}
        admin_id = str(item.get("admin_id", ""))
        # Resolve the trusted Firebase identity for display; never expose the UID
        # when an account has been removed or cannot be read.
        actor_email = "Administrator"
        if admin_id:
            try:
                actor_email = auth.get_user(admin_id).email or "Administrator"
            except Exception:
                actor_email = "Administrator"
        logs.append(AdminLog(timestamp=item["timestamp"], event_type="administrator", description=str(item.get("action", "Administrator activity")), actor=actor_email, severity="info", details={"target_type": item.get("target_type"), "target_id": item.get("target_id")}))
    for snapshot in event_snapshots:
        item = snapshot.to_dict() or {}
        failed = item.get("event_type") == "chat_failed"
        logs.append(AdminLog(timestamp=item["occurred_at"], event_type=str(item.get("event_type", "system")), description="Chat request failed" if failed else "Chat request completed", actor="system", severity="error" if failed else "info", details={"model_id": item.get("model_id"), "failure_category": item.get("failure_category"), "duration_ms": item.get("duration_ms")}))
    return sorted(logs, key=lambda item: item.timestamp, reverse=True)


def list_documents() -> list[AdminDocument]:
    settings = get_settings()
    client = get_firestore_client()
    metadata = {}
    with firestore_operation():
        snapshots = client.collection("documents").stream(timeout=FIRESTORE_READ_TIMEOUT)
        for snapshot in client.collection("admin_document_metadata").stream(timeout=FIRESTORE_READ_TIMEOUT):
            metadata[snapshot.id] = snapshot.to_dict() or {}
    result: list[AdminDocument] = []
    for snapshot in snapshots:
        record = DocumentRecord.model_validate(snapshot.to_dict())
        filename = record.source.replace("\\", "/").rsplit("/", 1)[-1]
        extra = metadata.get(record.document_id, {})
        uploader_email = extra.get("uploaded_by")
        if uploader_email:
            try:
                uploader_email = auth.get_user(str(uploader_email)).email or "Administrator"
            except Exception:
                uploader_email = "Administrator"
        result.append(AdminDocument(
            document_id=record.document_id,
            filename=filename,
            title=extra.get("title") or Path(filename).stem,
            category=extra.get("category") or (record.active.category if record.active else "Uncategorised"),
            version=record.active.version if record.active else record.last_attempt.version,
            uploaded_at=record.updated_at,
            status=record.last_attempt.status,
            source=record.source,
            chunk_count=record.active.chunk_count if record.active else 0,
            uploaded_by=uploader_email,
        ))
    return sorted(result, key=lambda item: item.uploaded_at, reverse=True)


def process_upload(admin: AuthenticatedUser, file: UploadFile, category: str | None) -> AdminDocument:
    """Persist a DOCX inside the configured raw-data tree, then publish atomically."""
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Only .docx documents are supported.")
    settings = get_settings()
    safe_name = Path(file.filename).name
    destination = settings.raw_data_dir / "admin" / f"{uuid4().hex}_{safe_name}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as stream:
        stream.write(file.file.read())
    try:
        result = publish_document(destination, settings, KnowledgeBaseStore(get_firestore_client()))
        if not result.persisted or result.status not in {"processed", "unchanged", "needs_review"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, result.error or "Document processing failed.")
        client = get_firestore_client()
        client.collection("admin_document_metadata").document(result.document_id).set({
            "title": Path(safe_name).stem,
            "category": category.strip() if category and category.strip() else "Uncategorised",
            "uploaded_by": admin.uid,
            "uploaded_at": utc_now(),
        }, merge=True)
        audit(admin, "document_processed", "document", result.document_id)
        return next(item for item in list_documents() if item.document_id == result.document_id)
    except Exception:
        # The source is retained for a failed attempt so the admin can diagnose/retry it.
        raise


def delete_document(admin: AuthenticatedUser, document_id: str) -> AdminDocument:
    match = next((item for item in list_documents() if item.document_id == document_id), None)
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    result = remove_document(match.source, get_settings(), KnowledgeBaseStore(get_firestore_client()))
    if not result.persisted:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, result.error or "Document removal failed.")
    audit(admin, "document_removed", "document", document_id)
    return next(item for item in list_documents() if item.document_id == document_id)


def update_document(admin: AuthenticatedUser, document_id: str, title: str | None, category: str | None) -> AdminDocument:
    match = next((item for item in list_documents() if item.document_id == document_id), None)
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    values = {key: value for key, value in {"title": title, "category": category}.items() if value is not None}
    if values:
        get_firestore_client().collection("admin_document_metadata").document(document_id).set(values, merge=True)
    audit(admin, "document_updated", "document", document_id)
    return next(item for item in list_documents() if item.document_id == document_id)


def list_users() -> list[AdminUser]:
    client = get_firestore_client()
    profiles: dict[str, dict] = {}
    with firestore_operation():
        for snapshot in client.collection("users").stream(timeout=FIRESTORE_READ_TIMEOUT):
            data = snapshot.to_dict() or {}
            if data.get("user_id"):
                profiles[data["user_id"]] = data
    users: list[AdminUser] = []
    for item in auth.list_users().iterate_all():
        profile = profiles.get(item.uid, {})
        role = (item.custom_claims or {}).get("role", profile.get("role", "passenger"))
        users.append(AdminUser(
            user_id=item.uid, email=item.email, name=item.display_name,
            role=role if role in {"passenger", "admin"} else "passenger",
            disabled=item.disabled, email_verified=item.email_verified,
            created_at=_to_datetime(item.user_metadata.creation_timestamp // 1000 if item.user_metadata.creation_timestamp else None),
            last_login=_to_datetime(item.user_metadata.last_sign_in_timestamp // 1000 if item.user_metadata.last_sign_in_timestamp else None),
        ))
    return sorted(users, key=lambda item: (item.email or "").lower())


def update_user_role(admin: AuthenticatedUser, user_id: str, role: str) -> AdminUser:
    if user_id == admin.uid:
        raise HTTPException(status.HTTP_409_CONFLICT, "Use a separate administrator account to change your own role.")
    user = auth.get_user(user_id)
    claims = dict(user.custom_claims or {})
    claims["role"] = role
    auth.set_custom_user_claims(user_id, claims)
    audit(admin, "user_role_changed", "user", user_id)
    return next(item for item in list_users() if item.user_id == user_id)


def update_user_status(admin: AuthenticatedUser, user_id: str, disabled: bool) -> AdminUser:
    if user_id == admin.uid:
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot disable your own administrator account.")
    auth.update_user(user_id, disabled=disabled)
    audit(admin, "user_status_changed", "user", user_id)
    return next(item for item in list_users() if item.user_id == user_id)


def list_conversations() -> list:
    client = get_firestore_client()
    with firestore_operation():
        snapshots = client.collection("conversations").stream(timeout=FIRESTORE_READ_TIMEOUT)
    store = ConversationStore(client)
    details = []
    for snapshot in snapshots:
        raw = snapshot.to_dict() or {}
        conversation_id = raw.get("conversation_id", snapshot.id)
        conversation = store._read_conversation(snapshot, conversation_id)
        details.append(conversation)
    return sorted(details, key=lambda item: item.started_at, reverse=True)


def list_conversation_summaries() -> list[AdminConversationSummary]:
    """Join conversation records with Firebase emails and message counts for admins."""
    conversations = list_conversations()
    client = get_firestore_client()
    with firestore_operation():
        message_snapshots = list(client.collection("messages").stream(timeout=FIRESTORE_READ_TIMEOUT))
    counts: dict[str, list[int]] = {}
    for snapshot in message_snapshots:
        raw = snapshot.to_dict() or {}
        key = raw.get("conversation_id")
        if key:
            bucket = counts.setdefault(key, [0, 0])
            bucket[0] += 1
            bucket[1] += raw.get("sender_type") == "customer"
    result = []
    for conversation in conversations:
        email = None
        try:
            email = auth.get_user(conversation.user_id).email
        except Exception:
            pass
        message_count, customer_count = counts.get(conversation.conversation_id, [0, 0])
        result.append(AdminConversationSummary(
            conversation_id=conversation.conversation_id,
            user_id=conversation.user_id,
            user_email=email,
            title=conversation.title,
            language=conversation.language,
            status=conversation.status,
            started_at=conversation.started_at,
            message_count=message_count,
            customer_message_count=customer_count,
        ))
    return result


def get_conversation(conversation_id: str):
    client = get_firestore_client()
    store = ConversationStore(client)
    conversation = store._read_conversation(store._conversation_reference(conversation_id).get(timeout=FIRESTORE_READ_TIMEOUT), conversation_id)
    return store._messages(conversation_id)


def analytics(period_days: int = 30) -> AdminAnalytics:
    """Build honest aggregates from persisted records; no sample values are generated."""
    ended = utc_now()
    started = ended - timedelta(days=period_days)
    client = get_firestore_client()

    with firestore_operation():
        user_count = sum(1 for _ in auth.list_users().iterate_all())
        document_snapshots = list(client.collection("documents").stream(timeout=FIRESTORE_READ_TIMEOUT))
        conversation_snapshots = list(client.collection("conversations").stream(timeout=FIRESTORE_READ_TIMEOUT))
        event_snapshots = list(client.collection("operational_events").stream(timeout=FIRESTORE_READ_TIMEOUT))
        feedback_snapshots = list(client.collection("feedback").stream(timeout=FIRESTORE_READ_TIMEOUT))

    conversations = []
    for snapshot in conversation_snapshots:
        raw = snapshot.to_dict() or {}
        conversation = ConversationStore._read_conversation(snapshot, raw.get("conversation_id", snapshot.id))
        if conversation.started_at >= started:
            conversations.append(conversation)

    events: list[OperationalEvent] = []
    for snapshot in event_snapshots:
        event = OperationalEvent.model_validate(snapshot.to_dict())
        if event.occurred_at >= started:
            events.append(event)

    feedback: list[Feedback] = []
    for snapshot in feedback_snapshots:
        item = Feedback.model_validate(snapshot.to_dict())
        if item.created_at >= started:
            feedback.append(item)

    language_counts = Counter(item.language for item in conversations)
    model_counts = Counter(item.model_id for item in events if item.model_id)
    daily_counts = Counter(item.started_at.date().isoformat() for item in conversations)
    completed = [item for item in events if item.event_type == "chat_completed"]
    ratings = Counter(str(item.rating) for item in feedback)
    average_rating = sum(item.rating for item in feedback) / len(feedback) if feedback else 0.0

    # Include zero-count days so a quiet period is visible rather than disappearing
    # from the chart and looking like missing data.
    dates = [(started + timedelta(days=offset)).date().isoformat() for offset in range(period_days + 1)]
    message_counts: Counter[str] = Counter()
    first_messages: dict[str, str] = {}
    with firestore_operation():
        message_snapshots = list(client.collection("messages").stream(timeout=FIRESTORE_READ_TIMEOUT))
    for snapshot in message_snapshots:
        raw = snapshot.to_dict() or {}
        conversation_id = raw.get("conversation_id")
        if conversation_id:
            message_counts[conversation_id] += 1
            if raw.get("sender_type") == "customer":
                first_messages.setdefault(conversation_id, raw.get("message_text", ""))

    recent_activity: list[dict[str, str]] = []
    with firestore_operation():
        activity_snapshots = list(client.collection("admin_activity").stream(timeout=FIRESTORE_READ_TIMEOUT))
    for snapshot in activity_snapshots:
        raw = snapshot.to_dict() or {}
        timestamp = raw.get("timestamp")
        if timestamp and timestamp >= started:
            recent_activity.append({
                "action": str(raw.get("action", "Administrator activity")),
                "target_type": str(raw.get("target_type", "system")),
                "timestamp": timestamp.isoformat(),
            })

    recent_conversations = [
        {
            "conversation_id": item.conversation_id,
            "user_id": item.user_id,
            "first_message": first_messages.get(item.conversation_id),
            "language": item.language,
            "started_at": item.started_at.isoformat(),
            "message_count": message_counts[item.conversation_id],
        }
        for item in sorted(conversations, key=lambda value: value.started_at, reverse=True)[:5]
    ]

    return AdminAnalytics(
        period_days=period_days,
        period_started_at=started,
        period_ended_at=ended,
        totals={
            "users": user_count,
            "conversations": len(conversations),
            "documents": len(document_snapshots),
            "active_documents": sum(1 for item in document_snapshots if (item.to_dict() or {}).get("active")),
            "system_events": len(events),
        },
        conversation_activity=[{"date": day, "count": daily_counts[day]} for day in dates],
        languages=dict(sorted(language_counts.items())),
        models=dict(sorted(model_counts.items())),
        feedback={"count": len(feedback), "average_rating": round(average_rating, 2), "distribution": dict(sorted(ratings.items()))},
        performance={"completed_count": len(completed), "average_response_ms": round(sum(item.duration_ms for item in completed) / len(completed), 2) if completed else 0.0},
        responses=dict(Counter(item.response_source for item in events if item.response_source)),
        failures=dict(Counter(item.failure_category for item in events if item.failure_category)),
        rating_distribution=dict(sorted(ratings.items())),
        recent_conversations=recent_conversations,
        recent_activity=sorted(recent_activity, key=lambda item: item["timestamp"], reverse=True)[:8],
    )
