import logging
from typing import Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.document_chunk import DocumentChunk


logger = logging.getLogger(__name__)
LanguagePreference = Literal["en", "sw", "mixed"]


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(strict=True, min_length=1, max_length=4000)
    language: LanguagePreference | None = None
    top_k: int | None = Field(default=None, strict=True, ge=1, le=20)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        if not value.strip() or any(
            unicodedata.category(char) in {"Cc", "Cs"} and char not in "\r\n\t" for char in value
        ):
            logger.warning("Rejected empty or invalid retrieval question.")
            raise ValueError("Question must contain text without invalid control characters.")
        return value


class RankedChunk(BaseModel):
    rank: int = Field(ge=1)
    score: float = Field(allow_inf_nan=False)
    chunk: DocumentChunk


class RetrievalResponse(BaseModel):
    question: str
    normalized_query: str
    language: Literal["en", "sw", "mixed", "unknown"]
    language_source: Literal["selected", "unspecified"]
    top_k: int
    returned_count: int
    results: list[RankedChunk]
    score_type: Literal["cosine_similarity"] = "cosine_similarity"
    score_definition: str = "1 - cosine distance; higher is closer, not confidence or proof of answerability."
    evidence_status: Literal["not_assessed"] = "not_assessed"
    retrieval_ms: float
