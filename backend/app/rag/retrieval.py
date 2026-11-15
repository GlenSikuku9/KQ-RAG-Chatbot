from functools import lru_cache
import logging
import re
from time import perf_counter

from chromadb.errors import ChromaError
from fastapi import HTTPException
from filelock import Timeout

from app.config import Settings, get_settings
from app.models.retrieval import RankedChunk, RetrievalRequest, RetrievalResponse
from app.rag.embeddings import EmbeddingConfigurationError, EmbeddingInputError, get_embedder
from app.rag.vector_index import IndexStateError, VectorIndex


logger = logging.getLogger(__name__)

# Small, reviewed bilingual expansions help multilingual E5 connect common
# Kiswahili passenger wording to the English policy corpus. The passenger's
# original question is still preserved for generation and audit records.
_SWAHILI_QUERY_CONCEPTS = (
    (re.compile(r"\bmizigo\b", re.IGNORECASE), "baggage allowance"),
    (re.compile(r"\bdaraja la biashara\b", re.IGNORECASE), "business class"),
    (re.compile(r"\bulaya\b", re.IGNORECASE), "Europe"),
    (re.compile(r"\bkusahihisha jina\b", re.IGNORECASE), "name correction"),
    (re.compile(r"\b(?:ada|gharama|\w*gharimu)\b", re.IGNORECASE), "fee cost"),
    (re.compile(r"\btiketi\b", re.IGNORECASE), "ticket"),
    (re.compile(r"\bkimataifa\b", re.IGNORECASE), "international"),
    (re.compile(r"\bkubadilisha nafasi\b", re.IGNORECASE), "change booking"),
    (re.compile(r"\b(?:hati|nyaraka) za safari\b", re.IGNORECASE), "travel documents passport visa"),
)


def normalized_search_query(question: str, language: str | None) -> str:
    """Normalize whitespace and append only concepts present in Swahili text."""

    normalized = re.sub(r"\s+", " ", question).strip()
    if language not in {"sw", "mixed"}:
        return normalized
    concepts = [
        concept
        for pattern, concept in _SWAHILI_QUERY_CONCEPTS
        if pattern.search(normalized)
        and concept.lower() not in normalized.lower()
    ]
    if not concepts:
        return normalized
    return f"{normalized} | {'; '.join(dict.fromkeys(concepts))}"


class RetrievalService:
    def __init__(self, index: VectorIndex, settings: Settings):
        self.index = index
        self.settings = settings

    def retrieve(self, request: RetrievalRequest) -> RetrievalResponse:
        # Preserve the original for generation/audit; only whitespace changes in the search query.
        normalized = normalized_search_query(
            request.resolved_question or request.question,
            request.language,
        )
        limit = request.top_k if request.top_k is not None else self.settings.retrieval_top_k
        start = perf_counter()
        try:
            hits = self.index.search(normalized, limit)
        except EmbeddingInputError as exc:
            logger.warning("Retrieval question exceeds model input constraints.")
            raise HTTPException(422, "Question exceeds the embedding model input limit; shorten it and retry.") from exc
        except (IndexStateError, EmbeddingConfigurationError, OSError, RuntimeError, ChromaError, Timeout) as exc:
            logger.error("Retrieval unavailable (%s).", type(exc).__name__)
            raise HTTPException(503, "Retrieval is unavailable. Check the local model and published vector index.") from exc
        return RetrievalResponse(
            question=request.question, normalized_query=normalized,
            language=request.language or "unknown",
            language_source=(
                "selected"
                if request.language
                else "unspecified"
            ),
            top_k=limit, returned_count=len(hits),
            results=[RankedChunk(rank=index, score=hit.score, chunk=hit.chunk)
                     for index, hit in enumerate(hits, 1)],
            retrieval_ms=round((perf_counter() - start) * 1000, 3),
        )


@lru_cache(maxsize=1)
def get_retrieval_service() -> RetrievalService:
    settings = get_settings()
    try:
        index = VectorIndex(settings.chroma_persist_dir, get_embedder(), settings.embedding_batch_size)
    except (OSError, RuntimeError, ChromaError) as exc:
        logger.error("Retrieval initialization unavailable (%s).", type(exc).__name__)
        raise HTTPException(503, "Cannot initialize the local vector index.") from exc
    return RetrievalService(index, settings)
