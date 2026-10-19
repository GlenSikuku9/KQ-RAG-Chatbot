"""Request and response shapes for provider connectivity (I13)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.context import ContextSource
from app.models.retrieval import RetrievalRequest


class GenerationRequest(BaseModel):
    """An administrator-only provider probe, not the passenger chat request."""
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(strict=True, min_length=1, max_length=12000)
    model_id: str | None = Field(default=None, min_length=1, max_length=100)
    system_instruction: str | None = Field(default=None, max_length=4000)

    @field_validator("prompt", "system_instruction")
    @classmethod
    def require_non_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Text must not be blank.")
        return value


class GenerationResponse(BaseModel):
    model_id: str
    provider: Literal["openrouter", "google_ai_studio"]
    upstream_model: str
    text: str
    provider_request_id: str | None = None


class ConfiguredModel(BaseModel):
    """Safe metadata: a credential is never included."""
    model_id: str
    provider: Literal["openrouter", "google_ai_studio"]
    upstream_model: str
    configured: bool
    is_default: bool


class GroundedAnswerRequest(RetrievalRequest):
    """A retrieval question plus the optional configured generation-model alias."""

    model_id: str | None = Field(default=None, min_length=1, max_length=100)


class GroundedAnswerResponse(BaseModel):
    """An answer and the exact retrieved chunks available to its generation."""

    answer: str
    language: Literal["en", "sw", "mixed", "unknown"]
    decision: Literal["context_available", "clarification_required", "fallback"]
    generation_called: bool
    model: GenerationResponse | None = None
    sources: list[ContextSource]
