from pydantic import BaseModel, Field


class DocumentChunk(BaseModel):
    """A small searchable piece of a knowledge-base document."""

    chunk_id: str = Field(..., description="Identifier used by ChromaDB.")
    source: str = Field(..., description="Original file name this chunk came from.")
    file_type: str = Field(..., description="Original file extension, for example docx or pdf.")
    category: str = Field(..., description="Knowledge-base category for filtering and citations.")
    chunk_index: int = Field(..., description="Position of this chunk inside the source document.")
    text: str = Field(..., description="Chunk text that will later be embedded and searched.")
