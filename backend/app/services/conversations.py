"""Passenger-owned conversation storage and atomic message traceability (I15)."""

import logging
from uuid import uuid4

from fastapi import HTTPException, status
from google.cloud.firestore_v1 import Client, DocumentReference, DocumentSnapshot, Transaction
from google.cloud.firestore_v1.base_query import FieldFilter
from pydantic import ValidationError

from app.models.conversation import (
    Conversation, ConversationCreateRequest, ConversationDetail, ConversationMessage,
    ConversationStatus, MessageChunk, ResponseSource, SenderType, SendMessageRequest,
    SendMessageResponse,
)
from app.models.database import encode_document_id, utc_now
from app.models.generation import GroundedAnswerResponse
from app.models.user import AuthenticatedUser
from app.rag.grounded_generation import GroundedAnswerService, get_grounded_answer_service
from app.services.firestore_client import (
    FIRESTORE_READ_TIMEOUT, firestore_operation, get_firestore_client, run_transaction,
)


logger = logging.getLogger(__name__)


class ConversationStore:
    """All conversation reads validate ownership before returning stored data."""

    def __init__(self, client: Client) -> None:
        self.client = client

    def _conversation_reference(self, conversation_id: str) -> DocumentReference:
        return self.client.collection("conversations").document(encode_document_id(conversation_id))

    def _message_reference(self, message_id: str) -> DocumentReference:
        return self.client.collection("messages").document(encode_document_id(message_id))

    def _message_chunk_reference(self, message_chunk_id: str) -> DocumentReference:
        return self.client.collection("message_chunks").document(encode_document_id(message_chunk_id))

    @staticmethod
    def _read_conversation(snapshot: DocumentSnapshot, conversation_id: str) -> Conversation:
        if not snapshot.exists:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
        try:
            conversation = Conversation.model_validate(snapshot.to_dict())
            if conversation.conversation_id != conversation_id:
                raise ValueError("Conversation identity mismatch.")
            return conversation
        except (ValidationError, ValueError) as error:
            logger.error("Stored conversation failed validation.")
            raise HTTPException(500, "Stored conversation is invalid; contact the administrator.") from error

    @staticmethod
    def _assert_owner(conversation: Conversation, user: AuthenticatedUser) -> None:
        if conversation.user_id != user.uid:
            # Return 404 to avoid confirming whether another passenger's ID exists.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")

    def create(self, user: AuthenticatedUser, request: ConversationCreateRequest) -> Conversation:
        now = utc_now()
        conversation = Conversation(
            conversation_id=uuid4().hex, user_id=user.uid,
            language=request.language or "unknown", status=ConversationStatus.ACTIVE,
            started_at=now,
        )

        def operation(transaction: Transaction) -> Conversation:
            # The random ID is generated before retries, so every retry writes one record.
            transaction.set(self._conversation_reference(conversation.conversation_id), conversation.model_dump())
            return conversation

        return run_transaction(self.client, operation)

    def list_for_user(self, user: AuthenticatedUser) -> list[Conversation]:
        with firestore_operation():
            snapshots = self.client.collection("conversations").where(
                filter=FieldFilter("user_id", "==", user.uid)
            ).order_by("started_at", direction="DESCENDING").stream(timeout=FIRESTORE_READ_TIMEOUT)
        conversations: list[Conversation] = []
        for snapshot in snapshots:
            conversation = self._read_conversation(snapshot, snapshot.to_dict().get("conversation_id", ""))
            self._assert_owner(conversation, user)
            conversations.append(conversation)
        return conversations

    def get_detail(self, user: AuthenticatedUser, conversation_id: str) -> ConversationDetail:
        with firestore_operation():
            conversation = self._read_conversation(
                self._conversation_reference(conversation_id).get(timeout=FIRESTORE_READ_TIMEOUT), conversation_id,
            )
        self._assert_owner(conversation, user)
        return ConversationDetail(conversation=conversation, messages=self._messages(conversation_id))

    def end(self, user: AuthenticatedUser, conversation_id: str) -> Conversation:
        reference = self._conversation_reference(conversation_id)

        def operation(transaction: Transaction) -> Conversation:
            conversation = self._read_conversation(
                reference.get(transaction=transaction, timeout=FIRESTORE_READ_TIMEOUT), conversation_id,
            )
            self._assert_owner(conversation, user)
            if conversation.status == ConversationStatus.ENDED:
                return conversation
            ended = conversation.model_copy(update={"status": ConversationStatus.ENDED, "ended_at": utc_now()})
            transaction.set(reference, ended.model_dump())
            return ended

        return run_transaction(self.client, operation)

    def save_exchange(
        self, user: AuthenticatedUser, conversation_id: str, request: SendMessageRequest,
        generated: GroundedAnswerResponse,
    ) -> SendMessageResponse:
        """Persist both messages and all cited chunks in one Firestore transaction."""
        conversation_reference = self._conversation_reference(conversation_id)
        customer_id, chatbot_id = uuid4().hex, uuid4().hex
        now = utc_now()

        def operation(transaction: Transaction) -> SendMessageResponse:
            conversation = self._read_conversation(
                conversation_reference.get(transaction=transaction, timeout=FIRESTORE_READ_TIMEOUT), conversation_id,
            )
            self._assert_owner(conversation, user)
            if conversation.status != ConversationStatus.ACTIVE:
                raise HTTPException(status.HTTP_409_CONFLICT, "This conversation has ended. Start a new conversation.")

            language = generated.language
            customer = ConversationMessage(
                message_id=customer_id, conversation_id=conversation_id, sequence=conversation.next_sequence,
                sender_type=SenderType.CUSTOMER, message_text=request.question, language=language, timestamp=now,
            )
            model = generated.model
            source = ResponseSource.GENERATED if generated.generation_called else ResponseSource(generated.decision)
            chatbot = ConversationMessage(
                message_id=chatbot_id, conversation_id=conversation_id, sequence=conversation.next_sequence + 1,
                sender_type=SenderType.CHATBOT, message_text=generated.answer, language=language, timestamp=now,
                model_id=model.model_id if model else None,
                model_provider=model.provider if model else None,
                model_version=model.upstream_model if model else None,
                response_source=source,
                retrieved_chunks=[MessageChunk(
                    message_chunk_id=f"{chatbot_id}_{source_item.chunk.chunk_id}", message_id=chatbot_id,
                    chunk_id=source_item.chunk.chunk_id, citation_id=source_item.citation_id,
                    similarity_score=source_item.score, retrieval_rank=source_item.retrieval_rank,
                ) for source_item in generated.sources],
            )
            # First question becomes a helpful title without exposing generated text or changing later titles.
            title = conversation.title
            if conversation.next_sequence == 1:
                title = request.question.strip()[:120]
            updated = conversation.model_copy(update={
                "title": title, "language": language, "next_sequence": conversation.next_sequence + 2,
            })
            transaction.set(conversation_reference, updated.model_dump())
            transaction.set(self._message_reference(customer_id), customer.model_dump(exclude={"retrieved_chunks"}))
            transaction.set(self._message_reference(chatbot_id), chatbot.model_dump(exclude={"retrieved_chunks"}))
            for trace in chatbot.retrieved_chunks:
                transaction.set(self._message_chunk_reference(trace.message_chunk_id), trace.model_dump())
            return SendMessageResponse(conversation=updated, customer_message=customer, chatbot_message=chatbot)

        return run_transaction(self.client, operation)

    def _messages(self, conversation_id: str) -> list[ConversationMessage]:
        with firestore_operation():
            snapshots = self.client.collection("messages").where(
                filter=FieldFilter("conversation_id", "==", conversation_id)
            ).order_by("sequence").stream(timeout=FIRESTORE_READ_TIMEOUT)
        messages: list[ConversationMessage] = []
        for snapshot in snapshots:
            try:
                message = ConversationMessage.model_validate(snapshot.to_dict())
                if message.conversation_id != conversation_id:
                    raise ValueError("Message conversation mismatch.")
            except (ValidationError, ValueError) as error:
                logger.error("Stored conversation message failed validation.")
                raise HTTPException(500, "Stored conversation message is invalid; contact the administrator.") from error
            if message.sender_type == SenderType.CHATBOT:
                message.retrieved_chunks = self._message_chunks(message.message_id)
            messages.append(message)
        return messages

    def _message_chunks(self, message_id: str) -> list[MessageChunk]:
        with firestore_operation():
            snapshots = self.client.collection("message_chunks").where(
                filter=FieldFilter("message_id", "==", message_id)
            ).order_by("retrieval_rank").stream(timeout=FIRESTORE_READ_TIMEOUT)
        try:
            return [MessageChunk.model_validate(snapshot.to_dict()) for snapshot in snapshots]
        except ValidationError as error:
            logger.error("Stored message traceability failed validation.")
            raise HTTPException(500, "Stored message traceability is invalid; contact the administrator.") from error


class ConversationService:
    """Coordinates provider work outside the database transaction and persistence after it."""

    def __init__(self, store: ConversationStore, grounded_answers: GroundedAnswerService) -> None:
        self.store = store
        self.grounded_answers = grounded_answers

    def send(self, user: AuthenticatedUser, conversation_id: str, request: SendMessageRequest) -> SendMessageResponse:
        # Do not write a successful chatbot message until generation succeeds.
        generated = self.grounded_answers.answer(request)
        return self.store.save_exchange(user, conversation_id, request, generated)


def get_conversation_service() -> ConversationService:
    return ConversationService(ConversationStore(get_firestore_client()), get_grounded_answer_service())
