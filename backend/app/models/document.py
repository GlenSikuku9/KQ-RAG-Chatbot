from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, Field, computed_field


class TableRow(BaseModel):
    row_index: int = Field(ge=1)
    text: str
    is_header: bool = False


class DocumentBlock(BaseModel):
    kind: Literal["paragraph", "heading", "table"]
    text: str
    source_location: str
    section: str | None = None
    rows: list[TableRow] = Field(default_factory=list)
    keep_together: bool = False


class KnowledgeBaseDocument(BaseModel):
    """Text extracted from one source file in the Kenya Airways knowledge base."""

    source: str = Field(..., description="Source path relative to the raw-data directory.")
    file_type: Literal["docx"] = Field(..., description="Supported source format: docx.")
    category: str = Field(..., description="Knowledge-base category inferred from the file name.")
    text: str = Field(..., description="Clean text extracted from the document.")
    extraction_warnings: list[str] = Field(default_factory=list)
    blocks: list[DocumentBlock] = Field(default_factory=list)

    @computed_field
    @property
    def document_id(self) -> str:
        """Stable identity for a source path, independent of its current contents."""
        return sha256(self.source.replace("\\", "/").encode("utf-8")).hexdigest()

    @computed_field
    @property
    def version(self) -> str:
        """Extracted-content fingerprint, not an invented publisher revision."""
        content = {
            "text": self.text,
            "category": self.category,
            "blocks": [block.model_dump() for block in self.blocks],
        }
        serialized = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return "sha256:" + sha256(serialized.encode("utf-8")).hexdigest()
