"""Validated contracts for passenger-to-agent support handoffs."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.models.database import UTCDateTime


class SupportRequestType(str, Enum):
    LIVE = "live"
    TICKET = "ticket"


class SupportStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class SupportSender(str, Enum):
    PASSENGER = "passenger"
    AGENT = "agent"


class SupportRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=128)
    request_type: SupportRequestType
    reason: str = Field(min_length=1, max_length=120)
    issue_summary: str = Field(min_length=3, max_length=1000)


class SupportMessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_text: str = Field(min_length=1, max_length=16000)
    client_message_id: str = Field(min_length=8, max_length=128)


class SupportRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    reference: str = Field(min_length=1, max_length=32)
    conversation_id: str = Field(min_length=1, max_length=128)
    passenger_id: str = Field(min_length=1, max_length=128)
    passenger_name: str | None = Field(default=None, max_length=200)
    passenger_email: str | None = Field(default=None, max_length=320)
    request_type: SupportRequestType
    status: SupportStatus
    reason: str = Field(min_length=1, max_length=120)
    issue_summary: str = Field(min_length=3, max_length=1000)
    assigned_agent_id: str | None = Field(default=None, max_length=128)
    assigned_agent_name: str | None = Field(default=None, max_length=200)
    created_at: UTCDateTime
    accepted_at: UTCDateTime | None = None
    resolved_at: UTCDateTime | None = None
    cancelled_at: UTCDateTime | None = None

    @field_serializer("request_type", "status")
    def serialize_enums(
        self,
        value: SupportRequestType | SupportStatus,
    ) -> str:
        return value.value


class SupportMessage(BaseModel):
    message_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    sender_role: SupportSender
    sender_id: str = Field(min_length=1, max_length=128)
    sender_name: str | None = Field(default=None, max_length=200)
    message_text: str = Field(min_length=1, max_length=16000)
    timestamp: UTCDateTime
    client_message_id: str = Field(min_length=8, max_length=128)

    @field_serializer("sender_role")
    def serialize_sender(self, value: SupportSender) -> str:
        return value.value


class SupportFeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)


class SupportFeedback(BaseModel):
    request_id: str
    passenger_id: str
    rating: int = Field(ge=1, le=5)
    comment: str | None = None
    updated_at: UTCDateTime


class SupportRequestDetail(BaseModel):
    request: SupportRequest
    messages: list[SupportMessage]
    feedback: SupportFeedback | None = None
    ai_messages: list[dict] = Field(default_factory=list)


class SupportAvailability(BaseModel):
    available: bool
    live_agents: int = Field(ge=0)
    wait_seconds: int = Field(ge=30)
    clarification_attempts: int = Field(ge=1, le=10)


class AgentAvailability(BaseModel):
    agent_id: str
    agent_name: str | None = None
    available: bool
    last_seen: UTCDateTime


class AgentAvailabilityUpdate(BaseModel):
    available: bool


class SupportSummary(BaseModel):
    pending: int = 0
    active_live_chats: int = 0
    open_tickets: int = 0
    resolved: int = 0
    total: int = 0
    live: int = 0
    tickets: int = 0
    average_acceptance_seconds: float | None = None
    average_resolution_seconds: float | None = None


class SupportRequestPage(BaseModel):
    items: list[SupportRequest]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=50)
    agents: list[str] = Field(default_factory=list)


class SupportRequestTypeUpdate(BaseModel):
    request_type: Literal["ticket"] = "ticket"
