from typing import Literal

from pydantic import BaseModel, Field


class DocumentChunk(BaseModel):
    """A small searchable piece of a knowledge-base document."""

    chunk_id: str = Field(..., description="Identifier used by ChromaDB.")
    document_id: str = Field(..., description="Stable identity of the source document.")
    version: str = Field(..., description="Extracted-content fingerprint of the source version.")
    source: str = Field(..., description="Source path relative to the raw-data directory.")
    file_type: Literal["docx"] = Field(..., description="Supported source format: docx.")
    category: str = Field(..., description="Knowledge-base category for filtering and citations.")
    chunk_index: int = Field(..., description="Position of this chunk inside the source document.")
    text: str = Field(..., description="Chunk text that will later be embedded and searched.")
    section: str | None = Field(default=None, description="Explicit source heading path, when available.")
    page: int | None = Field(default=None, ge=1, description="Unset for DOCX without reliable rendered pagination.")
    source_locations: list[str] = Field(default_factory=list)
    table_location: str | None = None
    table_rows: list[int] = Field(default_factory=list)
