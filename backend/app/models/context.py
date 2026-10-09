from typing import Literal

from pydantic import BaseModel, Field

from app.models.document_chunk import DocumentChunk
from app.models.retrieval import RetrievalResponse


class ContextSource(BaseModel):
    citation_id: str
    retrieval_rank: int = Field(ge=1)
    score: float = Field(allow_inf_nan=False)
    chunk: DocumentChunk


class ExcludedChunk(BaseModel):
    chunk_id: str
    reason: Literal["below_similarity_threshold", "duplicate_content", "context_budget_exceeded"]
    duplicate_of: str | None = None


class PreparedContext(BaseModel):
    retrieval: RetrievalResponse
    decision: Literal["context_available", "clarification_required", "fallback"]
    reason: Literal[
        "selected_context", "no_retrieved_chunks", "below_similarity_threshold", "context_budget_exceeded",
    ]
    evidence_status: Literal["heuristic_only"] = "heuristic_only"
    generation_called: Literal[False] = False
    message: str | None
    context: str
    context_chars: int = Field(ge=0)
    max_context_chars: int = Field(gt=0)
    min_similarity: float = Field(ge=-1, le=1, allow_inf_nan=False)
    sources: list[ContextSource]
    excluded: list[ExcludedChunk]
