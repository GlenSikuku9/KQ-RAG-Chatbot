"""Passenger-owned conversation storage and atomic message traceability (I15)."""

import logging
from time import perf_counter
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
from app.services.operational_logging import OperationalLogger, get_operational_logger


logger = logging.getLogger(__name__)


def _source_title(source: str) -> str:
    name = source.replace("\\", "/").rsplit("/", 1)[-1]
    return name[:-5] if name.lower().endswith(".docx") else name


class ConversationStore:
    """All conversation reads validate ownership before returning stored data."""

    def __init__(self, client: Client) -> None:
        self.client = client

    def _conversation_reference(self, conversation_id: str) -> DocumentReference:
        return self.client.collection("conversations").document(encode_document_id(conversation_id))

    @staticmethod
    def _conversation_payload(conversation: Conversation) -> dict:
        # next_sequence is hidden from API clients but must be stored so later
        # turns keep passenger questions and answers in order.
        return {**conversation.model_dump(), "next_sequence": conversation.next_sequence}

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
            transaction.set(
                self._conversation_reference(conversation.conversation_id),
                self._conversation_payload(conversation),
            )
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
            transaction.set(reference, self._conversation_payload(ended))
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
            # Retrieval uses "clarification_required" while persisted messages use the
            # shorter "clarification" enum value. Keep that boundary explicit.
            if generated.generation_called:
                source = ResponseSource.GENERATED
            elif generated.decision == "clarification_required":
                source = ResponseSource.CLARIFICATION
            else:
                source = ResponseSource.FALLBACK
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
                    source_title=_source_title(source_item.chunk.source),
                    source_section=source_item.chunk.section,
                ) for source_item in generated.sources],
            )
            # First question becomes a helpful title without exposing generated text or changing later titles.
            title = conversation.title
            if conversation.next_sequence == 1:
                title = request.question.strip()[:120]
            updated = conversation.model_copy(update={
                "title": title, "language": language, "next_sequence": conversation.next_sequence + 2,
            })
            transaction.set(conversation_reference, self._conversation_payload(updated))
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

    def __init__(
        self, store: ConversationStore, grounded_answers: GroundedAnswerService, operational_logger: OperationalLogger,
    ) -> None:
        self.store = store
        self.grounded_answers = grounded_answers
        self.operational_logger = operational_logger

    def send(self, user: AuthenticatedUser, conversation_id: str, request: SendMessageRequest) -> SendMessageResponse:
        started = perf_counter()
        try:
            # Do not write a successful chatbot message until generation succeeds.
            generated = self.grounded_answers.answer(request)
            result = self.store.save_exchange(user, conversation_id, request, generated)
        except Exception as error:
            # Record only a failure category/timing. The logging failure must not hide
            # the original chat failure or turn it into a successful response.
            try:
                self.operational_logger.failed(error, (perf_counter() - started) * 1000)
            except HTTPException:
                logger.warning("Could not persist a privacy-minimised operational failure event.")
            raise
        try:
            self.operational_logger.completed(generated, (perf_counter() - started) * 1000)
        except Exception:
            # The chat exchange is already safely committed; avoid falsely reporting
            # it as failed merely because its non-critical analytics event is unavailable.
            logger.warning("Could not persist a privacy-minimised operational completion event.")
        return result


def get_conversation_service() -> ConversationService:
    client = get_firestore_client()
    return ConversationService(ConversationStore(client), get_grounded_answer_service(), OperationalLogger(client))
