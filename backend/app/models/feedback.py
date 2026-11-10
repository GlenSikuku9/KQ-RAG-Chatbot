"""Passenger feedback and privacy-minimised operational event models (I16)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.database import UTCDateTime


class FeedbackRequest(BaseModel):
    """A passenger may replace their single 1–5 star conversation rating and optional comment."""

    model_config = ConfigDict(extra="forbid")
    rating: int = Field(strict=True, ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)

    @field_validator("comment")
    @classmethod
    def reject_blank_comment(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Comment must contain text when provided.")
        return value


class Feedback(BaseModel):
    feedback_id: str = Field(min_length=1, max_length=512)
    user_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)
    created_at: UTCDateTime
    updated_at: UTCDateTime


class OperationalEvent(BaseModel):
    """Analytics data excludes questions, answers, UIDs, tokens, and credentials."""

    event_id: str = Field(min_length=1, max_length=128)
    event_type: Literal[
        "chat_completed",
        "chat_failed",
        "conversation_ended",
        "feedback_submitted",
        "feedback_updated",
        "flight_lookup_completed",
        "flight_lookup_failed",
        "oag_api_request",
    ]
    occurred_at: UTCDateTime
    duration_ms: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    model_id: str | None = Field(default=None, max_length=100)
    model_provider: str | None = Field(default=None, max_length=100)
    model_version: str | None = Field(default=None, max_length=300)
    response_source: Literal[
        "generated",
        "clarification",
        "fallback",
        "flight_information",
    ] | None = None
    failure_category: Literal["provider", "storage", "retrieval", "validation", "unknown"] | None = None
    failed_operation: str | None = Field(default=None, max_length=100)
    error_code: str | None = Field(default=None, max_length=100)
    error_summary: str | None = Field(default=None, max_length=500)
    request_id: str | None = Field(default=None, max_length=128)
    conversation_id: str | None = Field(default=None, max_length=128)
    actor: str | None = Field(default=None, max_length=40)
    model_selection_status: Literal["selected", "not_selected"] | None = None
    external_provider: str | None = Field(default=None, max_length=40)
    http_status: int | None = Field(default=None, ge=100, le=599)
    request_outcome: str | None = Field(default=None, max_length=50)
    attempt_number: int | None = Field(default=None, ge=1, le=10)
