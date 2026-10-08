from typing import Literal

from pydantic import BaseModel, Field


class DocumentChunk(BaseModel):
    """A small searchable piece of a knowledge-base document."""

    chunk_id: str = Field(..., description="Identifier used by ChromaDB.")
    source: str = Field(..., description="Source path relative to the raw-data directory.")
    file_type: Literal["docx"] = Field(..., description="Supported source format: docx.")
    category: str = Field(..., description="Knowledge-base category for filtering and citations.")
    chunk_index: int = Field(..., description="Position of this chunk inside the source document.")
    text: str = Field(..., description="Chunk text that will later be embedded and searched.")
