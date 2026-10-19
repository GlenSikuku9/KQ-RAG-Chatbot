"""Ownership-checked, one-record-per-ended-conversation feedback storage (I16)."""

import logging

from fastapi import HTTPException, status
from google.cloud.firestore_v1 import Client, Transaction
from pydantic import ValidationError

from app.models.conversation import Conversation, ConversationStatus
from app.models.database import encode_document_id, utc_now
from app.models.feedback import Feedback, FeedbackRequest
from app.models.user import AuthenticatedUser
from app.services.firestore_client import FIRESTORE_READ_TIMEOUT, get_firestore_client, run_transaction


logger = logging.getLogger(__name__)


class FeedbackService:
    def __init__(self, client: Client) -> None:
        self.client = client

    def submit(self, user: AuthenticatedUser, conversation_id: str, request: FeedbackRequest) -> Feedback:
        """Create or update one feedback document for a caller's ended conversation."""
        conversation_ref = self.client.collection("conversations").document(encode_document_id(conversation_id))
        # A deterministic association key makes concurrent submissions converge on one record.
        feedback_id = f"{user.uid}:{conversation_id}"
        feedback_ref = self.client.collection("feedback").document(encode_document_id(feedback_id))

        def operation(transaction: Transaction) -> Feedback:
            conversation = self._read_conversation(
                conversation_ref.get(transaction=transaction, timeout=FIRESTORE_READ_TIMEOUT), conversation_id,
            )
            if conversation.user_id != user.uid:
                # Do not disclose another passenger's conversation existence.
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
            if conversation.status != ConversationStatus.ENDED:
                raise HTTPException(status.HTTP_409_CONFLICT, "End the conversation before submitting feedback.")
            existing_snapshot = feedback_ref.get(transaction=transaction, timeout=FIRESTORE_READ_TIMEOUT)
            existing = self._read_feedback(existing_snapshot, feedback_id) if existing_snapshot.exists else None
            now = utc_now()
            feedback = Feedback(
                feedback_id=feedback_id, user_id=user.uid, conversation_id=conversation_id,
                rating=request.rating, comment=request.comment,
                created_at=existing.created_at if existing else now, updated_at=now,
            )
            transaction.set(feedback_ref, feedback.model_dump())
            return feedback

        return run_transaction(self.client, operation)

    @staticmethod
    def _read_conversation(snapshot, conversation_id: str) -> Conversation:
        if not snapshot.exists:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
        try:
            conversation = Conversation.model_validate(snapshot.to_dict())
            if conversation.conversation_id != conversation_id:
                raise ValueError("Conversation identity mismatch.")
            return conversation
        except (ValidationError, ValueError) as error:
            logger.error("Stored conversation failed validation during feedback submission.")
            raise HTTPException(500, "Stored conversation is invalid; contact the administrator.") from error

    @staticmethod
    def _read_feedback(snapshot, feedback_id: str) -> Feedback:
        try:
            feedback = Feedback.model_validate(snapshot.to_dict())
            if feedback.feedback_id != feedback_id:
                raise ValueError("Feedback identity mismatch.")
            return feedback
        except (ValidationError, ValueError) as error:
            logger.error("Stored feedback failed validation.")
            raise HTTPException(500, "Stored feedback is invalid; contact the administrator.") from error


def get_feedback_service() -> FeedbackService:
    return FeedbackService(get_firestore_client())
