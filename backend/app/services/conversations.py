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
from app.models.generation import ConfiguredModel, GroundedAnswerResponse
from app.models.user import AuthenticatedUser
from app.rag.grounded_generation import GroundedAnswerService, get_grounded_answer_service
from app.services.firestore_client import (
    FIRESTORE_READ_TIMEOUT, firestore_operation, get_firestore_client, run_transaction,
)
from app.services.operational_logging import OperationalLogger, get_operational_logger


logger = logging.getLogger(__name__)


def conversation_title_from_question(question: str) -> str:
    """Create a compact topic title without changing the customer's message."""
    text = " ".join(question.split()).strip(" ?.!\t\r\n")
    lowered = text.lower()
    topics = (
        (("baggage", "luggage", "mizigo"), "Baggage allowance and checked luggage policy"),
        (("payment", "pay", "malipo"), "Payment methods and processing options for travel"),
        (("refund", "marejesho"), "Refund eligibility and payment details for travel"),
        (("wheelchair", "magurudumu"), "Wheelchair assistance and special care requirements"),
        (("check in", "check-in", "kuingia"), "Online check-in and airport requirements guide"),
        (("booking", "reservation", "nafasi"), "Booking changes and reservation support options"),
        (("document", "passport", "visa", "nyaraka"), "Travel documents and international journey requirements"),
        (("upgrade", "kuboresha"), "Flight upgrade options and refund information"),
    )
    for keywords, title in topics:
        if any(keyword in lowered for keyword in keywords):
            return title
    words = [word.strip(",:;()[]{}") for word in text.split() if word.strip(",:;()[]{}")]
    # Keep a fallback useful and bounded; CSS also clamps it to two lines.
    selected = words[:9] or ["New conversation"]
    title = " ".join(selected)
    return title[0].upper() + title[1:]


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
            ).stream(timeout=FIRESTORE_READ_TIMEOUT)
        conversations: list[Conversation] = []
        for snapshot in snapshots:
            conversation = self._read_conversation(snapshot, snapshot.to_dict().get("conversation_id", ""))
            self._assert_owner(conversation, user)
            conversations.append(conversation)
        # Sorting after the ownership-filtered read avoids requiring a Firestore
        # composite index for the passenger history screen.
        return sorted(conversations, key=lambda item: item.started_at, reverse=True)

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
        *,
        create_if_missing: bool = False,
    ) -> SendMessageResponse:
        """Persist both messages and all cited chunks in one Firestore transaction."""
        conversation_reference = self._conversation_reference(conversation_id)
        customer_id, chatbot_id = uuid4().hex, uuid4().hex
        now = utc_now()

        def operation(transaction: Transaction) -> SendMessageResponse:
            snapshot = conversation_reference.get(
                transaction=transaction,
                timeout=FIRESTORE_READ_TIMEOUT,
            )
            if create_if_missing:
                if snapshot.exists:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        "Conversation already exists.",
                    )
                conversation = Conversation(
                    conversation_id=conversation_id,
                    user_id=user.uid,
                    language=request.language or "unknown",
                    status=ConversationStatus.ACTIVE,
                    started_at=now,
                )
            else:
                conversation = self._read_conversation(
                    snapshot,
                    conversation_id,
                )
            self._assert_owner(conversation, user)
            if conversation.status != ConversationStatus.ACTIVE:
                raise HTTPException(status.HTTP_409_CONFLICT, "This conversation has ended. Start a new conversation.")
            if conversation.support_mode == "human":
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "A support agent is handling this conversation. Send the message through the active support case.",
                )

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
                    source_url=source_item.chunk.source_url,
                ) for source_item in generated.sources],
            )
            # Store a separate topic title; the original customer question remains unchanged.
            title = conversation.title
            if conversation.next_sequence == 1:
                title = conversation_title_from_question(request.question)
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

    def create_exchange(
        self,
        user: AuthenticatedUser,
        request: SendMessageRequest,
        generated: GroundedAnswerResponse,
    ) -> SendMessageResponse:
        """Atomically create the conversation together with its first exchange."""
        return self.save_exchange(
            user,
            uuid4().hex,
            request,
            generated,
            create_if_missing=True,
        )

    def _messages(self, conversation_id: str) -> list[ConversationMessage]:
        with firestore_operation():
            snapshots = self.client.collection("messages").where(
                filter=FieldFilter("conversation_id", "==", conversation_id)
            ).stream(timeout=FIRESTORE_READ_TIMEOUT)
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
        return sorted(messages, key=lambda item: item.sequence)

    def _message_chunks(self, message_id: str) -> list[MessageChunk]:
        with firestore_operation():
            snapshots = self.client.collection("message_chunks").where(
                filter=FieldFilter("message_id", "==", message_id)
            ).stream(timeout=FIRESTORE_READ_TIMEOUT)
        try:
            chunks = [MessageChunk.model_validate(snapshot.to_dict()) for snapshot in snapshots]
            return sorted(chunks, key=lambda item: item.retrieval_rank)
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

    def start(
        self,
        user: AuthenticatedUser,
        request: SendMessageRequest,
    ) -> SendMessageResponse:
        """Generate first, then atomically create a non-empty conversation."""
        return self._send(
            user,
            None,
            request,
        )

    def send(self, user: AuthenticatedUser, conversation_id: str, request: SendMessageRequest) -> SendMessageResponse:
        return self._send(
            user,
            conversation_id,
            request,
        )

    def _send(
        self,
        user: AuthenticatedUser,
        conversation_id: str | None,
        request: SendMessageRequest,
    ) -> SendMessageResponse:
        started = perf_counter()
        request_id = uuid4().hex
        selected_model = None

        # Resolve safe model metadata before retrieval/generation so a later
        # provider or storage failure can still identify the selected model.
        try:
            selected_model = (
                self.grounded_answers.generation_service.selected_model(
                    request.model_id,
                )
            )
        except Exception:
            # Some deterministic fallback responses never select a model. The
            # answer pipeline remains authoritative for whether that is an error.
            pass

        try:
            # Do not write a successful chatbot message until generation succeeds.
            generated = self.grounded_answers.answer(request)
        except Exception as error:
            failure_category = self.operational_logger.category(error)
            failed_operation = {
                "provider": "generate_model_response",
                "retrieval": "retrieve_knowledge",
                "validation": "validate_chat_request",
                "storage": "access_chat_dependencies",
            }.get(failure_category, "prepare_chat_answer")
            try:
                self.operational_logger.failed(
                    error,
                    (perf_counter() - started) * 1000,
                    model=selected_model,
                    failed_operation=failed_operation,
                    request_id=request_id,
                    failure_category=failure_category,
                )
            except HTTPException:
                logger.warning("Could not persist a privacy-minimised operational failure event.")
            raise

        try:
            result = (
                self.store.create_exchange(
                    user,
                    request,
                    generated,
                )
                if conversation_id is None
                else self.store.save_exchange(
                    user,
                    conversation_id,
                    request,
                    generated,
                )
            )
        except Exception as error:
            # Generation succeeded, so this is explicitly a persistence failure.
            generated_model = generated.model
            model = selected_model
            if generated_model is not None:
                model = ConfiguredModel(
                    model_id=generated_model.model_id,
                    provider=generated_model.provider,
                    upstream_model=generated_model.upstream_model,
                    configured=True,
                    is_default=request.model_id is None,
                )
            try:
                self.operational_logger.failed(
                    error,
                    (perf_counter() - started) * 1000,
                    model=model,
                    failed_operation="store_chat_exchange",
                    request_id=request_id,
                    failure_category="storage",
                )
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
