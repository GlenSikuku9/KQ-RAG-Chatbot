from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import HTTPException

from app.config import Settings
from app.models.database import utc_now
from app.models.document import KnowledgeBaseDocument, document_id_for_source
from app.models.document_chunk import DocumentChunk
from app.models.ingestion import ChunkingSpec, IngestionResult, ProcessingAttempt
from app.rag.chunking import CHUNKING_VERSION, chunk_documents
from app.rag.document_loader import (
    DocumentExtractionError, discover_documents, load_knowledge_base_document, source_path,
)
from app.services.knowledge_base import KnowledgeBaseStore


logger = logging.getLogger(__name__)
OUTPUT_FILE_NAME = "kq_document_chunks.json"
REPORT_FILE_NAME = "kq_ingestion_report.json"


@dataclass
class PreparedDocument:
    source: str
    attempt: ProcessingAttempt
    document: KnowledgeBaseDocument | None = None
    chunks: list[DocumentChunk] = field(default_factory=list)


def chunking_spec(settings: Settings) -> ChunkingSpec:
    return ChunkingSpec(
        revision=CHUNKING_VERSION, size=settings.rag_chunk_size, overlap=settings.rag_chunk_overlap,
    )


def prepare_document(file_path: Path, settings: Settings) -> PreparedDocument:
    source = source_path(file_path, settings.raw_data_dir)
    spec = chunking_spec(settings)
    try:
        document = load_knowledge_base_document(file_path, settings.raw_data_dir)
    except DocumentExtractionError as exc:
        logger.error("Document preparation failed for %s: %s", source, exc)
        return PreparedDocument(source, ProcessingAttempt(
            status="failed", chunking=spec, completed_at=utc_now(), error=str(exc),
        ))
    chunks = chunk_documents([document], spec.size, spec.overlap)
    attempt = ProcessingAttempt(
        status="needs_review" if document.extraction_warnings else "processed",
        version=document.version, chunk_count=len(chunks), chunking=spec,
        completed_at=utc_now(), warnings=document.extraction_warnings,
    )
    return PreparedDocument(source, attempt, document, chunks)


def publish_document(
    file_path: Path, settings: Settings, store: KnowledgeBaseStore,
) -> IngestionResult:
    """Trusted synchronous operation; admin authentication belongs at the calling route."""
    source = source_path(file_path, settings.raw_data_dir)
    attempted_version = None
    try:
        previous = store.read(source)
        expected = previous.revision if previous else None
        prepared = prepare_document(file_path, settings)
        attempted_version = prepared.attempt.version
        try:
            return store.save_attempt(source, expected, prepared.attempt, prepared.chunks)
        except HTTPException as exc:
            if exc.status_code != 422:
                raise
            # Oversize content must have a durable failed attempt, not just a CLI message.
            failed = prepared.attempt.model_copy(update={
                "status": "failed", "chunk_count": 0, "error": str(exc.detail),
                "warnings": [],
            })
            return store.save_attempt(source, expected, failed, [])
    except HTTPException as exc:
        logger.error("Ingestion could not be persisted for %s (HTTP %s).", source, exc.status_code)
        return IngestionResult(
            document_id=document_id_for_source(source), source=source,
            status="conflict" if exc.status_code == 409 else "failed", persisted=False,
            attempted_version=attempted_version, error=str(exc.detail),
        )


def remove_document(source: str, settings: Settings, store: KnowledgeBaseStore) -> IngestionResult:
    """Remove stored content only; never delete the original file."""
    canonical = source_path(settings.raw_data_dir / source, settings.raw_data_dir)
    try:
        previous = store.read(canonical)
        attempt = ProcessingAttempt(status="removed", chunking=chunking_spec(settings), completed_at=utc_now())
        return store.save_attempt(canonical, previous.revision if previous else None, attempt, [])
    except HTTPException as exc:
        logger.error("Document removal failed for %s (HTTP %s).", canonical, exc.status_code)
        return IngestionResult(
            document_id=document_id_for_source(canonical), source=canonical,
            status="conflict" if exc.status_code == 409 else "failed", persisted=False,
            error=str(exc.detail),
        )


def write_json_atomic(path: Path, payload: dict) -> None:
    """Never replace a valid report with a partially written JSON file."""
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError) as exc:
        logger.error("Cannot save ingestion output %s (%s).", path.name, type(exc).__name__)
        raise
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def preview_documents(settings: Settings) -> dict:
    """Build a review-only export; failures are explicit and never publish/delete content."""
    prepared = [prepare_document(path, settings) for path in discover_documents(settings.raw_data_dir)]
    documents = [item.document for item in prepared if item.document is not None]
    chunks = [chunk for item in prepared for chunk in item.chunks]
    return {
        "purpose": "offline_review_only",
        "document_count": len(documents),
        "attempted_document_count": len(prepared),
        "chunk_count": len(chunks),
        "chunk_size": settings.rag_chunk_size,
        "chunk_overlap": settings.rag_chunk_overlap,
        "chunking_version": CHUNKING_VERSION,
        "review_required_count": sum(item.attempt.status == "needs_review" for item in prepared),
        "failed_count": sum(item.attempt.status == "failed" for item in prepared),
        "documents": [
            {**document.model_dump(),
             "extraction_status": "needs_review" if document.extraction_warnings else "extracted"}
            for document in documents
        ],
        "processing_results": [
            {"document_id": document_id_for_source(item.source), "source": item.source,
             **item.attempt.model_dump(mode="json"), "persisted": False}
            for item in prepared
        ],
        "chunks": [chunk.model_dump() for chunk in chunks],
    }


def publication_report(results: list[IngestionResult]) -> dict:
    return {
        "completed_at": utc_now().isoformat(),
        "operation": "firestore_ingestion",
        "results": [result.model_dump(mode="json") for result in results],
        "failed_count": sum(result.status in {"failed", "conflict"} for result in results),
        "review_required_count": sum(result.status == "needs_review" for result in results),
    }
