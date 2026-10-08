from typing import Literal

from pydantic import BaseModel, Field


class KnowledgeBaseDocument(BaseModel):
    """Text extracted from one source file in the Kenya Airways knowledge base."""

    source: str = Field(..., description="Source path relative to the raw-data directory.")
    file_type: Literal["docx"] = Field(..., description="Supported source format: docx.")
    category: str = Field(..., description="Knowledge-base category inferred from the file name.")
    text: str = Field(..., description="Clean text extracted from the document.")
    extraction_warnings: list[str] = Field(default_factory=list)
