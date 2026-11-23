import logging
from typing import Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.document_chunk import DocumentChunk


logger = logging.getLogger(__name__)
LanguagePreference = Literal["en", "sw", "mixed"]
DetectedLanguage = Literal["en", "sw", "mixed", "unknown"]


class ConversationContextMessage(BaseModel):
    """A bounded prior message used for interpretation, never policy evidence."""

    conversation_id: str = Field(min_length=1, max_length=128)
    sender_type: Literal["customer", "chatbot"]
    message_text: str = Field(min_length=1, max_length=4000)


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(strict=True, min_length=1, max_length=4000)
    language: LanguagePreference | None = None
    previous_language: DetectedLanguage | None = None
    conversation_context: list[ConversationContextMessage] = Field(
        default_factory=list,
        max_length=10,
    )
    resolved_question: str | None = Field(
        default=None,
        min_length=1,
        max_length=4000,
        exclude=True,
    )
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
    # Cosine similarity remains separate because the context threshold is
    # calibrated for this scale, not for BM25 or reciprocal-rank scores.
    score: float = Field(allow_inf_nan=False)
    chunk: DocumentChunk
    dense_rank: int | None = Field(default=None, ge=1)
    lexical_rank: int | None = Field(default=None, ge=1)
    fusion_score: float | None = Field(default=None, ge=0)


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
    ranking_method: Literal["dense_cosine", "hybrid_rrf"] = "dense_cosine"
    evidence_status: Literal["not_assessed"] = "not_assessed"
    retrieval_ms: float
