from functools import lru_cache
import logging
import re
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

# These patterns require live/private records rather than static policy text.
# They intentionally avoid broad words such as "seat" or "flight" alone so
# ordinary policy questions continue through retrieval.
_UNSUPPORTED_QUERY_PATTERNS = (
    re.compile(
        r"\b(?:current|today|now|leo|sasa)\b.{0,45}\b(?:gate|lango)\b"
        r"|\b(?:gate|lango)\b.{0,45}\b(?:current|today|now|leo|sasa)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:bank account balance|account balance|salio la akaunti.{0,20}benki)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bbooking\s+[a-z0-9]{5,}\b.{0,50}\bseat\b"
        r"|\bseat\b.{0,50}\bbooking\s+[a-z0-9]{5,}\b",
        re.IGNORECASE,
    ),
)


def is_unsupported_policy_query(question: str) -> bool:
    """Return true only for explicit live/private-data requests."""

    return any(
        pattern.search(question)
        for pattern in _UNSUPPORTED_QUERY_PATTERNS
    )


def build_context(retrieval: RetrievalResponse, settings: Settings) -> PreparedContext:
    sources: list[ContextSource] = []
    excluded: list[ExcludedChunk] = []
    blocks: list[str] = []
    document_citations: dict[str, str] = {}
    used = 0
    eligible = 0
    if is_unsupported_policy_query(retrieval.question):
        language = "en" if retrieval.language == "unknown" else retrieval.language
        return PreparedContext(
            retrieval=retrieval,
            decision="fallback",
            reason="unsupported_query",
            message=FALLBACK_MESSAGES[language],
            context="",
            context_chars=0,
            max_context_chars=settings.context_max_chars,
            min_similarity=settings.context_min_similarity,
            sources=[],
            excluded=[],
        )

    for hit in retrieval.results:
        chunk = hit.chunk
        if not chunk.text.strip():
            logger.error("Retrieved chunk has no usable policy text.")
            raise HTTPException(503, "Retrieved policy content is invalid. Check the vector index.")
        if hit.score < settings.context_min_similarity:
            excluded.append(ExcludedChunk(chunk_id=chunk.chunk_id, reason="below_similarity_threshold"))
            continue
        eligible += 1
        selected_for_document = [
            source
            for source in sources
            if source.chunk.document_id == chunk.document_id
        ]
        if (
            chunk.document_id not in document_citations
            and len(document_citations) >= settings.context_max_documents
        ):
            excluded.append(ExcludedChunk(
                chunk_id=chunk.chunk_id,
                reason="document_limit_exceeded",
            ))
            continue
        if (
            len(selected_for_document)
            >= settings.context_max_chunks_per_document
        ):
            excluded.append(ExcludedChunk(
                chunk_id=chunk.chunk_id,
                reason="document_chunk_limit_exceeded",
            ))
            continue
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
        # Chunks remain separate evidence records, while each stable document
        # receives one passenger-facing citation before the prompt is built.
        citation = document_citations.get(chunk.document_id)
        if citation is None:
            citation = f"S{len(document_citations) + 1}"
            document_citations[chunk.document_id] = citation
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
        "unsupported_query",
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
