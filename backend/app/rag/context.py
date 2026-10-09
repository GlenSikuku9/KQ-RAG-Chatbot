from functools import lru_cache
import logging
from typing import Literal

from fastapi import HTTPException

from app.config import Settings, get_settings
from app.models.context import ContextSource, ExcludedChunk, PreparedContext
from app.models.retrieval import RetrievalRequest, RetrievalResponse
from app.rag.retrieval import RetrievalService, get_retrieval_service


logger = logging.getLogger(__name__)
FALLBACK_MESSAGES = {
    "en": "I could not prepare supporting Kenya Airways policy information for this question. Please contact Kenya Airways customer support.",
    "sw": "Sijaweza kuandaa taarifa za sera za Kenya Airways zinazosaidia kujibu swali hili. Tafadhali wasiliana na huduma kwa wateja wa Kenya Airways.",
    "mixed": "Sijaweza kupata supporting Kenya Airways policy information. Tafadhali contact Kenya Airways customer support.",
}
CLARIFICATION_MESSAGES = {
    "en": "I could not find strong enough policy evidence. Please clarify the Kenya Airways service or policy you need.",
    "sw": "Sijapata taarifa za sera zinazolingana vya kutosha na swali lako. Tafadhali fafanua huduma au sera ya Kenya Airways unayoulizia.",
    "mixed": "Sijapata strong enough policy evidence. Please clarify huduma au sera ya Kenya Airways unayoulizia.",
}


def build_context(retrieval: RetrievalResponse, settings: Settings) -> PreparedContext:
    sources: list[ContextSource] = []
    excluded: list[ExcludedChunk] = []
    blocks: list[str] = []
    used = 0
    eligible = 0
    for hit in retrieval.results:
        chunk = hit.chunk
        if not chunk.text.strip():
            logger.error("Retrieved chunk has no usable policy text.")
            raise HTTPException(503, "Retrieved policy content is invalid. Check the vector index.")
        if hit.score < settings.context_min_similarity:
            excluded.append(ExcludedChunk(chunk_id=chunk.chunk_id, reason="below_similarity_threshold"))
            continue
        eligible += 1
        # Only remove exact, fully contained text with matching source context.
        # Partial overlap and table headers stay intact to preserve qualifications.
        duplicate = next((
            source for source in sources
            if source.chunk.document_id == chunk.document_id and source.chunk.version == chunk.version
            and source.chunk.section == chunk.section and source.chunk.table_location == chunk.table_location
            and chunk.source_locations
            and set(chunk.source_locations).issubset(source.chunk.source_locations)
            and chunk.text in source.chunk.text
        ), None)
        if duplicate is not None:
            excluded.append(ExcludedChunk(
                chunk_id=chunk.chunk_id, reason="duplicate_content", duplicate_of=duplicate.citation_id,
            ))
            continue
        citation = f"S{len(sources) + 1}"
        block = f"[{citation}]\n{chunk.text}"
        needed = len(block) + (2 if blocks else 0)
        # The budget includes citation markers and separators, never truncated policy text.
        if used + needed > settings.context_max_chars:
            excluded.append(ExcludedChunk(chunk_id=chunk.chunk_id, reason="context_budget_exceeded"))
            continue
        blocks.append(block)
        used += needed
        sources.append(ContextSource(
            citation_id=citation, retrieval_rank=hit.rank, score=hit.score, chunk=chunk,
        ))

    decision: Literal["context_available", "clarification_required", "fallback"]
    reason: Literal[
        "selected_context", "no_retrieved_chunks", "below_similarity_threshold", "context_budget_exceeded",
    ]
    language = "en" if retrieval.language == "unknown" else retrieval.language
    message = None
    if sources:
        decision, reason = "context_available", "selected_context"
    elif not retrieval.results:
        decision, reason = "fallback", "no_retrieved_chunks"
        message = FALLBACK_MESSAGES[language]
    elif not eligible:
        decision, reason = "clarification_required", "below_similarity_threshold"
        message = CLARIFICATION_MESSAGES[language]
    else:
        logger.warning("No complete eligible policy chunk fits the configured context budget.")
        decision, reason = "fallback", "context_budget_exceeded"
        message = FALLBACK_MESSAGES[language]
    return PreparedContext(
        retrieval=retrieval, decision=decision, reason=reason, message=message,
        context="\n\n".join(blocks), context_chars=used, max_context_chars=settings.context_max_chars,
        min_similarity=settings.context_min_similarity, sources=sources, excluded=excluded,
    )


class ContextService:
    def __init__(self, retrieval: RetrievalService, settings: Settings):
        self.retrieval = retrieval
        self.settings = settings

    def prepare(self, request: RetrievalRequest) -> PreparedContext:
        return build_context(self.retrieval.retrieve(request), self.settings)


@lru_cache(maxsize=1)
def get_context_service() -> ContextService:
    return ContextService(get_retrieval_service(), get_settings())
