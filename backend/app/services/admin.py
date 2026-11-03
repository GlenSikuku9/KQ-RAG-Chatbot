"""Administrator services. Routes authorize first; this module never trusts client roles."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import shutil
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from firebase_admin import auth

from app.config import get_settings
from app.models.admin import (
    DEFAULT_PASSENGER_THEME,
    AdminAnalytics,
    AdminAuditEvent,
    AdminConversationSummary,
    AdminConversationPage,
    AdminDocument,
    AdminDocumentPage,
    AdminDocumentVersion,
    AdminLog,
    AdminModel,
    AdminSearchResponse,
    AdminSearchResult,
    AdminUser,
    AdminUserPage,
    DocumentContent,
    EvaluationResult,
    PassengerTheme,
)
from app.models.generation import ConfiguredModel
from app.models.feedback import Feedback, OperationalEvent
from app.models.database import encode_document_id, utc_now
from app.models.ingestion import DocumentRecord
from app.models.user import AuthenticatedUser
from app.services.conversations import ConversationStore, conversation_title_from_question
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
        extra = metadata.get(record.document_id, {})
        filename = extra.get("original_filename") or record.source.replace("\\", "/").rsplit("/", 1)[-1]
        uploader_email = extra.get("uploaded_by")
        if uploader_email:
            try:
                uploader_email = auth.get_user(str(uploader_email)).email or "Administrator"
            except Exception:
                uploader_email = "Administrator"
        file_path = settings.raw_data_dir / record.source
        file_hash = extra.get("file_hash")
        if not file_hash and file_path.is_file():
            file_hash = sha256(file_path.read_bytes()).hexdigest()
        result.append(AdminDocument(
            document_id=record.document_id,
            filename=filename,
            title=extra.get("title") or Path(filename).stem,
            category=extra.get("category") or (record.active.category if record.active else "Uncategorised"),
            # The UI exposes a human-readable revision; the content fingerprint
            # remains available as file_hash and in the version-history records.
            version=f"v{int(extra.get('version_count', 1))}",
            uploaded_at=record.updated_at,
            status=record.last_attempt.status,
            source=record.source,
            chunk_count=record.active.chunk_count if record.active else 0,
            uploaded_by=uploader_email,
            file_hash=file_hash,
            version_count=int(extra.get("version_count", 1)),
        ))
    return sorted(result, key=lambda item: item.uploaded_at, reverse=True)


def query_documents(
    page: int,
    page_size: int,
    search: str = "",
    category: str | None = None,
    processing_status: str | None = None,
    sort_order: str = "newest",
) -> AdminDocumentPage:
    """Filter on the server so only the selected document page is serialized."""
    documents = list_documents()
    term = search.strip().lower()
    categories = sorted({item.category for item in documents})
    statuses = sorted({item.status for item in documents})
    filtered = [
        item for item in documents
        if (
            not term
            or term in item.filename.lower()
            or term in item.category.lower()
        )
        and (not category or item.category == category)
        and (not processing_status or item.status == processing_status)
    ]
    filtered.sort(
        key=lambda item: item.uploaded_at,
        reverse=sort_order != "oldest",
    )
    start = (page - 1) * page_size
    return AdminDocumentPage(
        items=filtered[start:start + page_size],
        total=len(filtered),
        page=page,
        page_size=page_size,
        categories=categories,
        statuses=statuses,
    )


def _document_for_filename(filename: str) -> AdminDocument | None:
    """Find the stable logical document represented by an uploaded filename."""
    for document in list_documents():
        existing_name = Path(document.filename).name.lower()
        if existing_name == filename.lower() or existing_name.endswith(f"_{filename.lower()}"):
            return document
    return None


def _version_reference(document_id: str, version_number: int) -> str:
    return f"document_versions/{document_id}/v{version_number}"


def _write_version_entry(
    admin: AuthenticatedUser, document: AdminDocument, version_number: int,
    file_hash: str, file_reference: str, status: str, uploaded_at: datetime,
) -> None:
    """Store immutable history separately from the active ingestion manifest."""
    client = get_firestore_client()
    client.collection("document_versions").document(f"{document.document_id}:v{version_number}").set({
        "version_id": f"{document.document_id}:v{version_number}",
        "document_id": document.document_id,
        "version_number": version_number,
        "version": f"v{version_number}",
        "file_hash": file_hash,
        "uploaded_at": uploaded_at,
        "uploaded_by": admin.uid,
        "file_reference": file_reference,
        "source": document.source,
        "status": status,
        "is_current": True,
        "title": document.title,
        "category": document.category,
    })


def _mark_previous_versions(document_id: str) -> None:
    client = get_firestore_client()
    with firestore_operation():
        snapshots = list(client.collection("document_versions").where("document_id", "==", document_id).stream(timeout=FIRESTORE_READ_TIMEOUT))
    for snapshot in snapshots:
        client.collection("document_versions").document(snapshot.id).set({"is_current": False}, merge=True)


def process_upload(admin: AuthenticatedUser, file: UploadFile, category: str | None) -> AdminDocument:
    """Persist a DOCX inside the configured raw-data tree, then publish atomically."""
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Only .docx documents are supported.")
    settings = get_settings()
    safe_name = Path(file.filename).name
    uploaded_bytes = file.file.read()
    file_hash = sha256(uploaded_bytes).hexdigest()
    existing = _document_for_filename(safe_name)
    destination = settings.raw_data_dir / (existing.source if existing else f"admin/{safe_name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    version_number = existing.version_count + 1 if existing else 1
    archive_reference = _version_reference(existing.document_id, version_number - 1) if existing else None
    archive_path = settings.raw_data_dir.parent / archive_reference if archive_reference else None
    if existing and destination.is_file():
        # Preserve the previous active bytes outside raw_data_dir so the indexer never
        # discovers archived versions as independent documents.
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(destination, archive_path)
    try:
        with destination.open("wb") as stream:
            stream.write(uploaded_bytes)
        result = publish_document(destination, settings, KnowledgeBaseStore(get_firestore_client()))
        if not result.persisted or result.status not in {"processed", "unchanged", "needs_review"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, result.error or "Document processing failed.")
        client = get_firestore_client()
        metadata = {
            "title": Path(safe_name).stem,
            "category": category.strip() if category and category.strip() else "Uncategorised",
            "uploaded_by": admin.uid,
            "uploaded_at": now,
            "original_filename": safe_name,
            "file_hash": file_hash,
            "version_count": version_number,
        }
        client.collection("admin_document_metadata").document(result.document_id).set(metadata, merge=True)
        if existing and archive_path:
            _mark_previous_versions(existing.document_id)
            _write_version_entry(admin, existing, version_number - 1, existing.file_hash or "unknown", str(archive_path), "archived", now)
        current = next(item for item in list_documents() if item.document_id == result.document_id)
        _mark_previous_versions(result.document_id)
        _write_version_entry(admin, current, version_number, file_hash, str(destination), "processed", now)
        audit(admin, "document_processed", "document", result.document_id)
        return next(item for item in list_documents() if item.document_id == result.document_id)
    except Exception:
        # The source is retained for a failed attempt so the admin can diagnose/retry it.
        raise


def list_document_versions(document_id: str) -> list[AdminDocumentVersion]:
    """Return immutable history in newest-first order without exposing credentials."""
    document = _document_match(document_id)
    client = get_firestore_client()
    with firestore_operation():
        snapshots = list(client.collection("document_versions").where("document_id", "==", document_id).stream(timeout=FIRESTORE_READ_TIMEOUT))
    versions = []
    for snapshot in snapshots:
        raw = snapshot.to_dict() or {}
        uploader = raw.get("uploaded_by")
        email = None
        if uploader:
            try:
                email = auth.get_user(str(uploader)).email
            except Exception:
                email = "Administrator"
        raw["uploaded_by"] = email or uploader
        versions.append(AdminDocumentVersion.model_validate(raw))
    if not versions:
        # Backfill a readable v1 entry for documents uploaded before I24 versioning.
        versions.append(AdminDocumentVersion(
            version_id=f"{document_id}:v1", document_id=document_id, version_number=1,
            version="v1", file_hash=document.file_hash or "unknown", uploaded_at=document.uploaded_at,
            uploaded_by=document.uploaded_by, file_reference=document.source, source=document.source,
            status=document.status, is_current=True, title=document.title, category=document.category,
        ))
    return sorted(versions, key=lambda item: item.version_number, reverse=True)


def restore_document_version(admin: AuthenticatedUser, document_id: str, version_number: int) -> AdminDocument:
    """Restore bytes into the active source, creating a new version number."""
    document = _document_match(document_id)
    versions = list_document_versions(document_id)
    selected = next((item for item in versions if item.version_number == version_number), None)
    if selected is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document version not found.")
    current_number = document.version_count
    if selected.version_number == current_number:
        raise HTTPException(status.HTTP_409_CONFLICT, "That version is already current.")
    selected_path = Path(selected.file_reference)
    if not selected_path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The archived document file is unavailable.")
    settings = get_settings()
    destination = settings.raw_data_dir / document.source
    now = utc_now()
    # Preserve the currently active bytes before replacing them with the selected version.
    current_archive = settings.raw_data_dir.parent / _version_reference(document_id, current_number)
    current_archive.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        shutil.copy2(destination, current_archive)
    _mark_previous_versions(document_id)
    client = get_firestore_client()
    client.collection("document_versions").document(f"{document_id}:v{current_number}").set({
        "file_reference": str(current_archive), "status": "archived", "is_current": False,
    }, merge=True)
    shutil.copy2(selected_path, destination)
    result = publish_document(destination, settings, KnowledgeBaseStore(client))
    if not result.persisted or result.status not in {"processed", "unchanged"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, result.error or "Document restoration failed during processing.")
    file_hash = sha256(destination.read_bytes()).hexdigest()
    new_number = max(item.version_number for item in versions) + 1
    client.collection("admin_document_metadata").document(document_id).set({
        "version_count": new_number, "file_hash": file_hash, "uploaded_at": now, "uploaded_by": admin.uid,
    }, merge=True)
    current = _document_match(document_id)
    _write_version_entry(admin, current, new_number, file_hash, str(destination), "processed", now)
    audit(admin, "document_version_restored", "document", document_id)
    return _document_match(document_id)


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


def _document_match(document_id: str) -> AdminDocument:
    match = next((item for item in list_documents() if item.document_id == document_id), None)
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    return match


def read_document_content(document_id: str) -> DocumentContent:
    """Read editable DOCX text for the protected administrator editor."""
    match = _document_match(document_id)
    path = get_settings().raw_data_dir / match.source
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The source DOCX is no longer available.")
    from app.rag.document_loader import _extract_document
    try:
        result = _extract_document(path)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    warning = "Some Word layout or tables may require review after saving." if result.warnings else None
    return DocumentContent(content=result.text, warning=warning)


def update_document_content(admin: AuthenticatedUser, document_id: str, content: str) -> AdminDocument:
    """Replace editable text, then run the normal atomic publication pipeline."""
    match = _document_match(document_id)
    path = get_settings().raw_data_dir / match.source
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The source DOCX is no longer available.")
    if not content.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Document content cannot be empty.")
    # python-docx writes editable paragraphs. Existing tables/images are not silently
    # interpreted as text; the editor warns that complex layout should be reviewed.
    from docx import Document
    now = utc_now()
    current_number = match.version_count
    current_archive = get_settings().raw_data_dir.parent / _version_reference(document_id, current_number)
    current_archive.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        # Content edits are revisions too: preserve the previous bytes before saving.
        shutil.copy2(path, current_archive)
    _mark_previous_versions(document_id)
    existing_versions = list_document_versions(document_id)
    if any(item.version_number == current_number for item in existing_versions):
        get_firestore_client().collection("document_versions").document(f"{document_id}:v{current_number}").set({
            "file_reference": str(current_archive), "status": "archived", "is_current": False,
        }, merge=True)
    else:
        _write_version_entry(admin, match, current_number, match.file_hash or "unknown", str(current_archive), "archived", now)
    document = Document(path)
    body = document._element.body
    for child in list(body):
        if child.tag.endswith("}sectPr"):
            continue
        body.remove(child)
    for paragraph in content.replace("\r\n", "\n").split("\n"):
        document.add_paragraph(paragraph)
    document.save(path)
    result = publish_document(path, get_settings(), KnowledgeBaseStore(get_firestore_client()))
    if not result.persisted or result.status not in {"processed", "unchanged"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, result.error or "Document processing failed after editing.")
    file_hash = sha256(path.read_bytes()).hexdigest()
    new_number = current_number + 1
    get_firestore_client().collection("admin_document_metadata").document(document_id).set({
        "version_count": new_number, "file_hash": file_hash, "uploaded_at": now, "uploaded_by": admin.uid,
    }, merge=True)
    current = _document_match(document_id)
    _write_version_entry(admin, current, new_number, file_hash, str(path), "processed", now)
    audit(admin, "document_content_updated", "document", document_id)
    return _document_match(document_id)


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


def query_users(
    page: int,
    page_size: int,
    search: str = "",
    role: str | None = None,
    account_status: str | None = None,
    verification: str | None = None,
) -> AdminUserPage:
    """Return only the filtered Firebase account page required by the table."""
    term = search.strip().lower()
    filtered = [
        item for item in list_users()
        if (
            not term
            or term in (item.name or "").lower()
            or term in (item.email or "").lower()
        )
        and (not role or item.role == role)
        and (
            not account_status
            or (account_status == "disabled") == item.disabled
        )
        and (
            not verification
            or (verification == "verified") == item.email_verified
        )
    ]
    start = (page - 1) * page_size
    return AdminUserPage(
        items=filtered[start:start + page_size],
        total=len(filtered),
        page=page,
        page_size=page_size,
    )


def query_conversation_summaries(
    page: int,
    page_size: int,
    search: str = "",
    language: str | None = None,
    conversation_status: str | None = None,
    started_from: datetime | None = None,
    started_to: datetime | None = None,
    sort_order: str = "newest",
) -> AdminConversationPage:
    """Read conversation summaries without scanning messages or making N+1 auth calls.

    `next_sequence` is updated transactionally with every two-message exchange,
    so existing records already contain the counts needed by the table. Complete
    messages and retrieval evidence remain deferred to the Inspect endpoint.
    """
    client = get_firestore_client()
    with firestore_operation():
        conversation_snapshots = list(
            client.collection("conversations")
            .stream(timeout=FIRESTORE_READ_TIMEOUT)
        )
        profile_snapshots = list(
            client.collection("users")
            .stream(timeout=FIRESTORE_READ_TIMEOUT)
        )
    emails = {
        str((snapshot.to_dict() or {}).get("user_id") or snapshot.id):
        (snapshot.to_dict() or {}).get("email")
        for snapshot in profile_snapshots
    }
    term = search.strip().lower()
    summaries: list[AdminConversationSummary] = []
    for snapshot in conversation_snapshots:
        raw = snapshot.to_dict() or {}
        conversation_id = str(raw.get("conversation_id") or snapshot.id)
        conversation = ConversationStore._read_conversation(
            snapshot,
            conversation_id,
        )
        email = emails.get(conversation.user_id)
        if term and term not in conversation.title.lower() and term not in (email or "").lower():
            continue
        if language and conversation.language != language:
            continue
        status_value = getattr(conversation.status, "value", conversation.status)
        if conversation_status and status_value != conversation_status:
            continue
        if started_from and conversation.started_at < started_from:
            continue
        if started_to and conversation.started_at > started_to:
            continue
        message_count = max(conversation.next_sequence - 1, 0)
        title = conversation.title.strip()
        if title == "New conversation" or len(title.split()) > 9:
            title = conversation_title_from_question(title)
        summaries.append(AdminConversationSummary(
            conversation_id=conversation_id,
            user_id=conversation.user_id,
            user_email=email,
            title=title,
            language=conversation.language,
            status=status_value,
            started_at=conversation.started_at,
            message_count=message_count,
            customer_message_count=message_count // 2,
        ))
    summaries.sort(
        key=lambda item: item.started_at,
        reverse=sort_order != "oldest",
    )
    start = (page - 1) * page_size
    return AdminConversationPage(
        items=summaries[start:start + page_size],
        total=len(summaries),
        page=page,
        page_size=page_size,
    )


def search_admin_records(query: str, limit: int = 6) -> AdminSearchResponse:
    """Search bounded data windows without sending whole collections to the browser.

    Firestore does not provide general substring search. This navbar search reads a
    capped window from each source (rather than streaming an unbounded collection)
    and returns only safe summaries. The cap keeps request cost predictable while
    preserving the existing database architecture and authorization boundary.
    """
    normalized = query.strip().lower()
    scan_limit = min(max(limit * 12, 48), 120)
    results: list[AdminSearchResult] = []

    # Firebase Auth is the trusted source for account email and display name.
    for item in auth.list_users(max_results=scan_limit).users:
        searchable = " ".join(filter(None, [item.display_name, item.email])).lower()
        if normalized in searchable:
            results.append(AdminSearchResult(
                result_type="user",
                result_id=item.uid,
                title=item.display_name or item.email or "Passenger account",
                description=item.email or "No email address available",
            ))
            if sum(result.result_type == "user" for result in results) >= limit:
                break

    client = get_firestore_client()
    with firestore_operation():
        conversation_snapshots = list(
            client.collection("conversations")
            .limit(scan_limit)
            .stream(timeout=FIRESTORE_READ_TIMEOUT)
        )
        metadata_snapshots = list(
            client.collection("admin_document_metadata")
            .limit(scan_limit)
            .stream(timeout=FIRESTORE_READ_TIMEOUT)
        )

    conversation_count = 0
    for snapshot in conversation_snapshots:
        raw = snapshot.to_dict() or {}
        title = str(raw.get("title") or "Conversation")
        if normalized not in title.lower():
            continue
        results.append(AdminSearchResult(
            result_type="conversation",
            result_id=str(raw.get("conversation_id") or snapshot.id),
            title=title,
            description=(
                f"{str(raw.get('language') or 'Unknown language')} · "
                f"{str(raw.get('status') or 'Unknown status')}"
            ),
        ))
        conversation_count += 1
        if conversation_count >= limit:
            break

    document_count = 0
    for snapshot in metadata_snapshots:
        raw = snapshot.to_dict() or {}
        title = str(raw.get("title") or raw.get("original_filename") or "Support document")
        searchable = " ".join([
            title,
            str(raw.get("original_filename") or ""),
            str(raw.get("category") or ""),
        ]).lower()
        if normalized not in searchable:
            continue
        results.append(AdminSearchResult(
            result_type="document",
            result_id=snapshot.id,
            title=title,
            description=str(raw.get("category") or "Uncategorised document"),
        ))
        document_count += 1
        if document_count >= limit:
            break

    return AdminSearchResponse(query=query.strip(), results=results)


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
    first_messages: dict[str, str] = {}
    for snapshot in message_snapshots:
        raw = snapshot.to_dict() or {}
        key = raw.get("conversation_id")
        if key:
            bucket = counts.setdefault(key, [0, 0])
            bucket[0] += 1
            bucket[1] += raw.get("sender_type") == "customer"
            if raw.get("sender_type") == "customer":
                first_messages.setdefault(key, str(raw.get("message_text", "")))
    result = []
    for conversation in conversations:
        email = None
        try:
            email = auth.get_user(conversation.user_id).email
        except Exception:
            pass
        message_count, customer_count = counts.get(conversation.conversation_id, [0, 0])
        stored_title = conversation.title.strip()
        # Older records stored the complete first question; derive a display title
        # without rewriting the original message or exposing its ID in the UI.
        display_title = conversation_title_from_question(first_messages.get(conversation.conversation_id, stored_title)) if (
            stored_title == "New conversation" or len(stored_title.split()) > 9
        ) else stored_title
        result.append(AdminConversationSummary(
            conversation_id=conversation.conversation_id,
            user_id=conversation.user_id,
            user_email=email,
            title=display_title,
            first_customer_message=first_messages.get(conversation.conversation_id),
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
        # Overview needs only the newest activity rows; the dedicated Logs page
        # keeps its own paginated query and is not affected by this limit.
        activity_snapshots = list(
            client.collection("admin_activity")
            .order_by("timestamp", direction="DESCENDING")
            .limit(6)
            .stream(timeout=FIRESTORE_READ_TIMEOUT)
        )
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
            "title": item.title,
            "first_message": first_messages.get(item.conversation_id),
            "language": item.language,
            "started_at": item.started_at.isoformat(),
            "message_count": message_counts[item.conversation_id],
        }
        for item in sorted(conversations, key=lambda value: value.started_at, reverse=True)[:6]
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
        recent_activity=sorted(recent_activity, key=lambda item: item["timestamp"], reverse=True)[:6],
    )
