"""Administrator-only document, user, conversation, and audit endpoints."""

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from firebase_admin import auth

from app.api.dependencies import require_admin
from app.models.admin import (
    AdminAnalytics,
    AdminConversationSummary,
    AdminConversationPage,
    AdminDocument,
    AdminDocumentPage,
    AdminDocumentVersion,
    AdminLog,
    AdminNotification,
    AdminModel,
    AdminSearchResponse,
    AdminUser,
    AdminUserPage,
    DefaultModelUpdate,
    DocumentContent,
    DocumentUpdate,
    EvaluationResult,
    PassengerTheme,
    UserDisplayNameUpdate,
    UserRoleUpdate,
    UserStatusUpdate,
)
from app.models.conversation import ConversationDetail
from app.models.user import AuthenticatedUser
from app.models.support import (
    AgentAvailability,
    AgentAvailabilityUpdate,
    SupportMessage,
    SupportMessageCreate,
    SupportRequest,
    SupportRequestDetail,
    SupportRequestPage,
    SupportSummary,
)
from app.services import admin as service
from app.services.conversations import ConversationStore
from app.services.firestore_client import get_firestore_client
from app.services.support import get_support_service

router = APIRouter()


@router.put(
    "/support/availability",
    response_model=AgentAvailability,
)
def update_support_availability(
    payload: AgentAvailabilityUpdate,
    admin: AuthenticatedUser = Depends(require_admin),
) -> AgentAvailability:
    return get_support_service().set_availability(
        admin,
        payload.available,
    )


@router.get(
    "/support/availability",
    response_model=AgentAvailability,
)
def read_support_availability(
    admin: AuthenticatedUser = Depends(require_admin),
) -> AgentAvailability:
    return get_support_service().agent_availability(admin)


@router.get("/support/summary", response_model=SupportSummary)
def support_summary(
    _: AuthenticatedUser = Depends(require_admin),
) -> SupportSummary:
    return get_support_service().summary()


@router.get("/support/requests", response_model=SupportRequestPage)
def support_requests(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    search: str = "",
    request_type: str | None = None,
    case_status: str | None = None,
    assigned_agent: str | None = None,
    sort_order: str = "newest",
    _: AuthenticatedUser = Depends(require_admin),
) -> SupportRequestPage:
    return get_support_service().query_admin(
        page,
        page_size,
        search,
        request_type,
        case_status,
        assigned_agent,
        sort_order,
    )


@router.get(
    "/support/requests/{request_id}",
    response_model=SupportRequestDetail,
)
def support_request_detail(
    request_id: str,
    _: AuthenticatedUser = Depends(require_admin),
) -> SupportRequestDetail:
    return get_support_service().admin_detail(request_id)


@router.post(
    "/support/requests/{request_id}/accept",
    response_model=SupportRequest,
)
def accept_support_request(
    request_id: str,
    admin: AuthenticatedUser = Depends(require_admin),
) -> SupportRequest:
    return get_support_service().accept(admin, request_id)


@router.post(
    "/support/requests/{request_id}/convert-to-ticket",
    response_model=SupportRequest,
)
def convert_support_request_to_ticket(
    request_id: str,
    admin: AuthenticatedUser = Depends(require_admin),
) -> SupportRequest:
    return get_support_service().convert_to_ticket(
        admin,
        request_id,
        require_owner=False,
    )


@router.post(
    "/support/requests/{request_id}/resolve",
    response_model=SupportRequest,
)
def resolve_support_request(
    request_id: str,
    admin: AuthenticatedUser = Depends(require_admin),
) -> SupportRequest:
    return get_support_service().resolve(admin, request_id)


@router.post(
    "/support/requests/{request_id}/reopen",
    response_model=SupportRequest,
)
def reopen_support_request(
    request_id: str,
    admin: AuthenticatedUser = Depends(require_admin),
) -> SupportRequest:
    return get_support_service().reopen(admin, request_id)


@router.post(
    "/support/requests/{request_id}/messages",
    response_model=SupportMessage,
)
def send_support_message(
    request_id: str,
    payload: SupportMessageCreate,
    admin: AuthenticatedUser = Depends(require_admin),
) -> SupportMessage:
    return get_support_service().send_message(
        admin,
        request_id,
        payload,
        as_agent=True,
    )


@router.get("/search", response_model=AdminSearchResponse)
def global_search(
    query: str,
    limit: int = 6,
    _: AuthenticatedUser = Depends(require_admin),
):
    """Search bounded administrator-safe summaries; credentials and messages stay private."""
    if len(query.strip()) < 2:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Enter at least two characters.",
        )
    if limit < 1 or limit > 10:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "The result limit must be between 1 and 10.",
        )
    return service.search_admin_records(query, limit)


@router.get("/models", response_model=list[AdminModel])
def models(_: AuthenticatedUser = Depends(require_admin)):
    return service.list_admin_models()


@router.put("/models/default", response_model=AdminModel)
def default_model(payload: DefaultModelUpdate, admin: AuthenticatedUser = Depends(require_admin)):
    return service.set_default_model(admin, payload.model_id)


@router.get("/evaluations", response_model=list[EvaluationResult])
def evaluations(_: AuthenticatedUser = Depends(require_admin)):
    return service.list_evaluations()


@router.get("/logs", response_model=list[AdminLog])
def logs(_: AuthenticatedUser = Depends(require_admin)):
    return service.list_logs()


@router.get("/notifications", response_model=list[AdminNotification])
def notifications(admin: AuthenticatedUser = Depends(require_admin)):
    return service.list_notifications(admin)


@router.put(
    "/notifications/read-all",
    response_model=list[AdminNotification],
)
def read_all_notifications(admin: AuthenticatedUser = Depends(require_admin)):
    return service.mark_all_notifications_read(admin)


@router.put(
    "/notifications/{event_id}/read",
    response_model=AdminNotification,
)
def read_notification(
    event_id: str,
    admin: AuthenticatedUser = Depends(require_admin),
):
    return service.mark_notification_read(admin, event_id)


@router.get("/settings/passenger-theme", response_model=PassengerTheme)
def read_passenger_theme(_: AuthenticatedUser = Depends(require_admin)):
    return service.get_passenger_theme()


@router.put("/settings/passenger-theme", response_model=PassengerTheme)
def save_passenger_theme(payload: PassengerTheme, admin: AuthenticatedUser = Depends(require_admin)):
    return service.save_passenger_theme(admin, payload)


@router.get("/analytics", response_model=AdminAnalytics)
def analytics(days: int = 30, _: AuthenticatedUser = Depends(require_admin)):
    if days < 1 or days > 365:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The reporting period must be between 1 and 365 days.")
    return service.analytics(days)


@router.get("/documents", response_model=list[AdminDocument])
def documents(_: AuthenticatedUser = Depends(require_admin)):
    return service.list_documents()


@router.get("/document-pages", response_model=AdminDocumentPage)
def document_page(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    search: str = "",
    category: str | None = None,
    processing_status: str | None = None,
    sort_order: str = "newest",
    _: AuthenticatedUser = Depends(require_admin),
):
    return service.query_documents(
        page,
        page_size,
        search,
        category,
        processing_status,
        sort_order,
    )


@router.post("/documents", response_model=AdminDocument, status_code=status.HTTP_201_CREATED)
def upload_document(file: UploadFile = File(...), category: str | None = Form(None), admin: AuthenticatedUser = Depends(require_admin)):
    return service.process_upload(admin, file, category)


@router.delete("/documents/{document_id}", response_model=AdminDocument)
def remove_document(document_id: str, admin: AuthenticatedUser = Depends(require_admin)):
    return service.delete_document(admin, document_id)


@router.patch("/documents/{document_id}", response_model=AdminDocument)
def update_document(document_id: str, payload: DocumentUpdate, admin: AuthenticatedUser = Depends(require_admin)):
    return service.update_document(admin, document_id, payload.title, payload.category)


@router.get("/documents/{document_id}/content", response_model=DocumentContent)
def document_content(document_id: str, _: AuthenticatedUser = Depends(require_admin)):
    return service.read_document_content(document_id)


@router.put("/documents/{document_id}/content", response_model=AdminDocument)
def save_document_content(document_id: str, payload: DocumentContent, admin: AuthenticatedUser = Depends(require_admin)):
    return service.update_document_content(admin, document_id, payload.content)


@router.get("/documents/{document_id}/versions", response_model=list[AdminDocumentVersion])
def document_versions(document_id: str, _: AuthenticatedUser = Depends(require_admin)):
    return service.list_document_versions(document_id)


@router.post("/documents/{document_id}/versions/{version_number}/restore", response_model=AdminDocument)
def restore_document_version(document_id: str, version_number: int, admin: AuthenticatedUser = Depends(require_admin)):
    return service.restore_document_version(admin, document_id, version_number)


@router.post("/documents/{document_id}/process", response_model=AdminDocument)
def process_document(document_id: str, admin: AuthenticatedUser = Depends(require_admin)):
    """Retry processing an existing source without allowing arbitrary filesystem paths."""
    item = next((document for document in service.list_documents() if document.document_id == document_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    settings = service.get_settings()
    source = settings.raw_data_dir / item.source
    result = service.publish_document(source, settings, service.KnowledgeBaseStore(service.get_firestore_client()))
    if not result.persisted:
        service.audit(
            admin,
            "document_processing_failed",
            "document",
            document_id,
            {
                "document_name": item.title,
                "failure_summary": "Document processing did not complete.",
            },
            status_value="failure",
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, result.error or "Document processing failed.")
    service.sync_vector_index()
    updated = next(
        document
        for document in service.list_documents()
        if document.document_id == document_id
    )
    service.audit(
        admin,
        "document_reprocessed",
        "document",
        document_id,
        {
            "document_name": updated.title,
            "document_id": updated.document_id,
            "document_version": updated.version,
            "processing_result": result.status,
        },
    )
    return updated


@router.get("/users", response_model=list[AdminUser])
def users(_: AuthenticatedUser = Depends(require_admin)):
    return service.list_users()


@router.get("/user-pages", response_model=AdminUserPage)
def user_page(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    search: str = "",
    role: str | None = None,
    account_status: str | None = None,
    verification: str | None = None,
    _: AuthenticatedUser = Depends(require_admin),
):
    return service.query_users(
        page,
        page_size,
        search,
        role,
        account_status,
        verification,
    )


@router.patch("/users/{user_id}/role", response_model=AdminUser)
def role(user_id: str, payload: UserRoleUpdate, admin: AuthenticatedUser = Depends(require_admin)):
    return service.update_user_role(admin, user_id, payload.role)


@router.patch("/users/{user_id}/status", response_model=AdminUser)
def user_status(user_id: str, payload: UserStatusUpdate, admin: AuthenticatedUser = Depends(require_admin)):
    return service.update_user_status(admin, user_id, payload.disabled)


@router.patch("/users/{user_id}/display-name", response_model=AdminUser)
def user_display_name(
    user_id: str,
    payload: UserDisplayNameUpdate,
    admin: AuthenticatedUser = Depends(require_admin),
):
    return service.update_user_display_name(
        admin,
        user_id,
        payload.display_name,
    )


@router.delete("/users/{user_id}")
def delete_user(
    user_id: str,
    admin: AuthenticatedUser = Depends(require_admin),
):
    service.delete_user_account(admin, user_id)
    return {"deleted": True, "user_id": user_id}


@router.get("/conversations")
def conversations(_: AuthenticatedUser = Depends(require_admin)):
    return service.list_conversation_summaries()


@router.get("/conversation-pages", response_model=AdminConversationPage)
def conversation_page(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    search: str = "",
    language: str | None = None,
    conversation_status: str | None = None,
    started_from: datetime | None = None,
    started_to: datetime | None = None,
    sort_order: str = "newest",
    _: AuthenticatedUser = Depends(require_admin),
):
    return service.query_conversation_summaries(
        page,
        page_size,
        search,
        language,
        conversation_status,
        started_from,
        started_to,
        sort_order,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def conversation(conversation_id: str, _: AuthenticatedUser = Depends(require_admin)):
    client = get_firestore_client()
    store = ConversationStore(client)
    item = store._read_conversation(
        store._conversation_reference(conversation_id).get(
            timeout=service.FIRESTORE_READ_TIMEOUT,
        ),
        conversation_id,
    )
    try:
        user_email = auth.get_user(item.user_id).email
    except Exception:
        user_email = None
    feedback_id = service.encode_document_id(
        f"{item.user_id}:{conversation_id}",
    )
    with service.firestore_operation():
        feedback_snapshot = client.collection("feedback").document(
            feedback_id,
        ).get(timeout=service.FIRESTORE_READ_TIMEOUT)
    feedback = (
        service.Feedback.model_validate(feedback_snapshot.to_dict())
        if feedback_snapshot.exists
        else None
    )
    return ConversationDetail(
        conversation=item,
        messages=store._messages(conversation_id),
        user_email=user_email,
        feedback=feedback,
    )


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    admin: AuthenticatedUser = Depends(require_admin),
):
    service.delete_conversation(admin, conversation_id)
    return {"deleted": True, "conversation_id": conversation_id}
