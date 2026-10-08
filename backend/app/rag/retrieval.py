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


class RetrievalService:
    def __init__(self, index: VectorIndex, settings: Settings):
        self.index = index
        self.settings = settings

    def retrieve(self, request: RetrievalRequest) -> RetrievalResponse:
        # Preserve the original for generation/audit; only whitespace changes in the search query.
        normalized = re.sub(r"\s+", " ", request.question).strip()
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
        # Language is a preference, not a detector output; no translation or guessed confidence.
        return RetrievalResponse(
            question=request.question, normalized_query=normalized,
            language=request.language or "unknown",
            language_source="selected" if request.language else "unspecified",
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
