"""HTTP contracts for protected administrator workflows (I20/I21)."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.conversation import ConversationDetail
from app.models.database import UTCDateTime


class AdminDocument(BaseModel):
    document_id: str
    filename: str
    title: str
    category: str
    version: str | None = None
    uploaded_at: UTCDateTime
    uploaded_by: str | None = None
    status: str
    source: str
    chunk_count: int = 0


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    category: str | None = Field(default=None, min_length=1, max_length=120)


class AdminUser(BaseModel):
    user_id: str
    email: str | None = None
    name: str | None = None
    role: Literal["passenger", "admin"]
    disabled: bool
    email_verified: bool
    created_at: UTCDateTime | None = None
    last_login: UTCDateTime | None = None


class UserStatusUpdate(BaseModel):
    disabled: bool


class UserRoleUpdate(BaseModel):
    role: Literal["passenger", "admin"]


class AdminConversation(BaseModel):
    detail: ConversationDetail
    user_email: str | None = None


class AdminAuditEvent(BaseModel):
    activity_id: str
    admin_id: str
    action: str
    target_type: str
    target_id: str
    timestamp: UTCDateTime
    status: Literal["success", "failure"] = "success"


class AdminAnalytics(BaseModel):
    """Aggregate values are always labelled with the period used to calculate them."""
    period_days: int
    period_started_at: UTCDateTime
    period_ended_at: UTCDateTime
    totals: dict[str, int]
    conversation_activity: list[dict[str, int | str]]
    languages: dict[str, int]
    models: dict[str, int]
    feedback: dict[str, float | int | dict[str, int]]
    performance: dict[str, float | int]
    responses: dict[str, int]
    failures: dict[str, int]
    rating_distribution: dict[str, int]
    recent_conversations: list[dict[str, str | int | None]]
    recent_activity: list[dict[str, str]]


DEFAULT_PASSENGER_THEME = {
    "primary": "#0b2e6f",
    "secondary": "#16458f",
    "accent": "#f5b335",
    "background": "#f4f7fa",
    "panel": "#ffffff",
    "user_message": "#d71920",
    "assistant_message": "#f1f5f9",
    "text": "#172033",
    "secondary_text": "#5f6b7b",
    "border": "#dbe2ea",
}


class PassengerTheme(BaseModel):
    """Only hexadecimal colour tokens are accepted; arbitrary CSS is never stored."""
    primary: str = DEFAULT_PASSENGER_THEME["primary"]
    secondary: str = DEFAULT_PASSENGER_THEME["secondary"]
    accent: str = DEFAULT_PASSENGER_THEME["accent"]
    background: str = DEFAULT_PASSENGER_THEME["background"]
    panel: str = DEFAULT_PASSENGER_THEME["panel"]
    user_message: str = DEFAULT_PASSENGER_THEME["user_message"]
    assistant_message: str = DEFAULT_PASSENGER_THEME["assistant_message"]
    text: str = DEFAULT_PASSENGER_THEME["text"]
    secondary_text: str = DEFAULT_PASSENGER_THEME["secondary_text"]
    border: str = DEFAULT_PASSENGER_THEME["border"]

    @field_validator("primary", "secondary", "accent", "background", "panel", "user_message", "assistant_message", "text", "secondary_text", "border")
    @classmethod
    def validate_hex(cls, value: str) -> str:
        import re
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("Colours must use six-digit hexadecimal values such as #0b2e6f.")
        return value.lower()


class AdminModel(BaseModel):
    model_id: str
    provider: str
    upstream_model: str
    language_support: list[str] = Field(default_factory=lambda: ["en", "sw", "mixed"])
    configured: bool
    is_default: bool
    usage_count: int = 0
    usage_percentage: float = 0.0
    average_response_ms: float | None = None
    successful_requests: int = 0
    failed_requests: int = 0


class DefaultModelUpdate(BaseModel):
    model_id: str = Field(min_length=1, max_length=100)


class EvaluationResult(BaseModel):
    run_id: str
    evaluated_at: UTCDateTime
    dataset_id: str
    model_id: str
    language: str
    sample_size: int = Field(ge=0)
    faithfulness: float | None = None
    answer_relevance: float | None = None
    context_precision: float | None = None
    context_recall: float | None = None
    average_response_ms: float | None = None


class AdminLog(BaseModel):
    timestamp: UTCDateTime
    event_type: str
    description: str
    actor: str
    severity: Literal["info", "warning", "error"]
    details: dict[str, str | int | float | None] = {}


class AdminConversationSummary(BaseModel):
    conversation_id: str
    user_id: str
    user_email: str | None = None
    title: str
    language: str
    status: str
    started_at: UTCDateTime
    message_count: int = 0
    customer_message_count: int = 0
