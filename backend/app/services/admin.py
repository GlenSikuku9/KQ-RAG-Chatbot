"""Administrator services. Routes authorize first; this module never trusts client roles."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from firebase_admin import auth

from app.config import get_settings
from app.models.admin import AdminAuditEvent, AdminDocument, AdminUser
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
            uploaded_by=extra.get("uploaded_by"),
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


def get_conversation(conversation_id: str):
    client = get_firestore_client()
    store = ConversationStore(client)
    conversation = store._read_conversation(store._conversation_reference(conversation_id).get(timeout=FIRESTORE_READ_TIMEOUT), conversation_id)
    return store._messages(conversation_id)
