"""Validated Firestore and HTTP models for passenger conversations (I15)."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.models.database import UTCDateTime
from app.models.generation import GroundedAnswerRequest, GenerationResponse


class ConversationStatus(str, Enum):
    ACTIVE = "active"
    ENDED = "ended"


class SenderType(str, Enum):
    CUSTOMER = "customer"
    CHATBOT = "chatbot"


class ResponseSource(str, Enum):
    GENERATED = "generated"
    CLARIFICATION = "clarification"
    FALLBACK = "fallback"


class ConversationCreateRequest(BaseModel):
    """An optional language is recorded as unknown rather than guessed."""

    model_config = ConfigDict(extra="forbid")
    language: Literal["en", "sw", "mixed"] | None = None


class Conversation(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    title: str = Field(default="New conversation", min_length=1, max_length=120)
    language: Literal["en", "sw", "mixed", "unknown"]
    status: ConversationStatus
    started_at: UTCDateTime
    ended_at: UTCDateTime | None = None
    # The counter is updated in the same transaction as messages, preserving order.
    next_sequence: int = Field(default=1, ge=1, exclude=True)

    @field_serializer("status")
    def serialize_status(self, value: ConversationStatus) -> str:
        return value.value


class MessageChunk(BaseModel):
    """Traceability record connecting one chatbot message to one retrieved chunk."""

    message_chunk_id: str = Field(min_length=1, max_length=512)
    message_id: str = Field(min_length=1, max_length=128)
    chunk_id: str = Field(min_length=1, max_length=512)
    citation_id: str = Field(min_length=2, max_length=32)
    similarity_score: float = Field(allow_inf_nan=False)
    retrieval_rank: int = Field(ge=1)


class ConversationMessage(BaseModel):
    message_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    sender_type: SenderType
    message_text: str = Field(min_length=1, max_length=16000)
    language: Literal["en", "sw", "mixed", "unknown"]
    timestamp: UTCDateTime
    # Customer messages deliberately have no model metadata.
    model_id: str | None = Field(default=None, max_length=100)
    model_provider: str | None = Field(default=None, max_length=100)
    model_version: str | None = Field(default=None, max_length=300)
    response_source: ResponseSource | None = None
    retrieved_chunks: list[MessageChunk] = Field(default_factory=list)

    @field_serializer("sender_type", "response_source")
    def serialize_enums(self, value: SenderType | ResponseSource | None) -> str | None:
        return value.value if value is not None else None


class SendMessageRequest(GroundedAnswerRequest):
    """A passenger question uses the same validation as grounded generation."""


class ConversationDetail(BaseModel):
    conversation: Conversation
    messages: list[ConversationMessage]


class SendMessageResponse(BaseModel):
    conversation: Conversation
    customer_message: ConversationMessage
    chatbot_message: ConversationMessage
