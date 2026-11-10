"""Passenger conversation routes; every read/write is scoped to the verified user."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import require_passenger_or_admin
from app.models.conversation import (
    Conversation, ConversationCreateRequest, ConversationDetail, SendMessageRequest, SendMessageResponse,
)
from app.models.user import AuthenticatedUser
from app.rag.generation import GenerationProviderError
from app.services.conversations import ConversationService, get_conversation_service
from app.services.oag_flights import OAGFlightError


router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("", response_model=Conversation, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreateRequest,
    current_user: AuthenticatedUser = Depends(require_passenger_or_admin),
) -> Conversation:
    """Start a conversation owned by the authenticated passenger only."""
    return get_conversation_service().store.create(current_user, payload)


@router.get("", response_model=list[Conversation])
def list_conversations(
    current_user: AuthenticatedUser = Depends(require_passenger_or_admin),
) -> list[Conversation]:
    """List only the caller's conversations, newest first."""
    try:
        return get_conversation_service().store.list_for_user(current_user)
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Conversation history failed to load (%s).", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Conversation history is temporarily unavailable. Please try again.",
        ) from error


@router.post("/messages", response_model=SendMessageResponse)
def start_with_message(
    payload: SendMessageRequest,
    current_user: AuthenticatedUser = Depends(require_passenger_or_admin),
) -> SendMessageResponse:
    """Create a conversation only when its first exchange can be persisted."""
    try:
        return get_conversation_service().start(current_user, payload)
    except GenerationProviderError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.detail,
        ) from error
    except OAGFlightError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.detail,
        ) from error


@router.get("/{conversation_id}", response_model=ConversationDetail)
def reopen_conversation(
    conversation_id: str,
    current_user: AuthenticatedUser = Depends(require_passenger_or_admin),
) -> ConversationDetail:
    """Reopen a caller-owned conversation together with source traceability."""
    try:
        return get_conversation_service().store.get_detail(current_user, conversation_id)
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Conversation history failed to reopen (%s).", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="This conversation is temporarily unavailable. Please try again.",
        ) from error


@router.post("/{conversation_id}/messages", response_model=SendMessageResponse)
def send_message(
    conversation_id: str,
    payload: SendMessageRequest,
    current_user: AuthenticatedUser = Depends(require_passenger_or_admin),
) -> SendMessageResponse:
    """Generate and atomically record a customer/chatbot message pair."""
    try:
        return get_conversation_service().send(current_user, conversation_id, payload)
    except GenerationProviderError as error:
        # A provider failure happens before persistence and cannot appear as an answer.
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    except OAGFlightError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.detail,
        ) from error


@router.post("/{conversation_id}/end", response_model=Conversation)
def end_conversation(
    conversation_id: str,
    current_user: AuthenticatedUser = Depends(require_passenger_or_admin),
) -> Conversation:
    """End one caller-owned conversation; later messages receive a conflict response."""
    return get_conversation_service().store.end(current_user, conversation_id)
