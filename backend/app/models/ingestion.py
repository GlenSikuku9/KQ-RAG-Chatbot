from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.config import validate_chunk_settings
from app.models.database import UTCDateTime
from app.models.document import document_id_for_source


class ChunkingSpec(BaseModel):
    revision: str
    size: int = Field(strict=True)
    overlap: int = Field(strict=True)

    @model_validator(mode="after")
    def validate_settings(self) -> Self:
        validate_chunk_settings(self.size, self.overlap)
        return self


class ActiveDocumentVersion(BaseModel):
    version: str
    category: str
    chunk_ids: list[str]
    chunk_count: int = Field(gt=0)
    chunking: ChunkingSpec
    published_at: UTCDateTime

    @model_validator(mode="after")
    def validate_chunks(self) -> Self:
        if self.chunk_count != len(self.chunk_ids) or len(set(self.chunk_ids)) != len(self.chunk_ids):
            raise ValueError("Active chunk identifiers do not match the recorded count.")
        return self


class ProcessingAttempt(BaseModel):
    status: Literal["processed", "needs_review", "failed", "removed"]
    version: str | None = None
    chunk_count: int = Field(default=0, ge=0)
    chunking: ChunkingSpec
    completed_at: UTCDateTime
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class DocumentRecord(BaseModel):
    document_id: str
    source: str
    file_type: Literal["docx"] = "docx"
    created_at: UTCDateTime
    updated_at: UTCDateTime
    revision: str
    active: ActiveDocumentVersion | None = None
    last_attempt: ProcessingAttempt

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if document_id_for_source(self.source) != self.document_id:
            raise ValueError("Document identity does not match its source.")
        if self.last_attempt.status == "removed" and self.active is not None:
            raise ValueError("Removed documents cannot have an active version.")
        return self


class IngestionResult(BaseModel):
    document_id: str
    source: str
    status: Literal["processed", "unchanged", "needs_review", "failed", "removed", "conflict"]
    persisted: bool
    active_version: str | None = None
    active_chunk_count: int | None = None
    attempted_version: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
