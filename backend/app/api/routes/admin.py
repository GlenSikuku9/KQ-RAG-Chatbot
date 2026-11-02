"""Administrator-only document, user, conversation, and audit endpoints."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.dependencies import require_admin
from app.models.admin import AdminAnalytics, AdminConversationSummary, AdminDocument, AdminLog, AdminModel, AdminUser, DefaultModelUpdate, DocumentUpdate, EvaluationResult, PassengerTheme, UserRoleUpdate, UserStatusUpdate
from app.models.conversation import ConversationDetail
from app.models.user import AuthenticatedUser
from app.services import admin as service
from app.services.conversations import ConversationStore
from app.services.firestore_client import get_firestore_client

router = APIRouter()


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


@router.post("/documents", response_model=AdminDocument, status_code=status.HTTP_201_CREATED)
def upload_document(file: UploadFile = File(...), category: str | None = Form(None), admin: AuthenticatedUser = Depends(require_admin)):
    return service.process_upload(admin, file, category)


@router.delete("/documents/{document_id}", response_model=AdminDocument)
def remove_document(document_id: str, admin: AuthenticatedUser = Depends(require_admin)):
    return service.delete_document(admin, document_id)


@router.patch("/documents/{document_id}", response_model=AdminDocument)
def update_document(document_id: str, payload: DocumentUpdate, admin: AuthenticatedUser = Depends(require_admin)):
    return service.update_document(admin, document_id, payload.title, payload.category)


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
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, result.error or "Document processing failed.")
    service.audit(admin, "document_reprocessed", "document", document_id)
    return next(document for document in service.list_documents() if document.document_id == document_id)


@router.get("/users", response_model=list[AdminUser])
def users(_: AuthenticatedUser = Depends(require_admin)):
    return service.list_users()


@router.patch("/users/{user_id}/role", response_model=AdminUser)
def role(user_id: str, payload: UserRoleUpdate, admin: AuthenticatedUser = Depends(require_admin)):
    return service.update_user_role(admin, user_id, payload.role)


@router.patch("/users/{user_id}/status", response_model=AdminUser)
def user_status(user_id: str, payload: UserStatusUpdate, admin: AuthenticatedUser = Depends(require_admin)):
    return service.update_user_status(admin, user_id, payload.disabled)


@router.get("/conversations")
def conversations(_: AuthenticatedUser = Depends(require_admin)):
    return service.list_conversation_summaries()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def conversation(conversation_id: str, _: AuthenticatedUser = Depends(require_admin)):
    client = get_firestore_client()
    store = ConversationStore(client)
    item = store._read_conversation(store._conversation_reference(conversation_id).get(), conversation_id)
    return ConversationDetail(conversation=item, messages=store._messages(conversation_id))
