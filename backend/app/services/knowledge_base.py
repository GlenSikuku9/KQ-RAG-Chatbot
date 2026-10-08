import logging
from uuid import uuid4

from fastapi import HTTPException
from google.cloud.firestore_v1 import Client, DocumentReference, Transaction
from google.cloud.firestore_v1._helpers import encode_dict
from google.cloud.firestore_v1.types import Document
from pydantic import ValidationError

from app.models.database import encode_document_id, utc_now
from app.models.document import document_id_for_source
from app.models.document_chunk import DocumentChunk
from app.models.ingestion import ActiveDocumentVersion, DocumentRecord, IngestionResult, ProcessingAttempt
from app.services.firestore_client import FIRESTORE_READ_TIMEOUT, firestore_operation, run_transaction


logger = logging.getLogger(__name__)
# Conservative per-document transaction budgets leave room for Firestore metadata/indexes.
MAX_TRANSACTION_WRITES = 400
MAX_RECORD_BYTES = 900_000
MAX_TRANSACTION_BYTES = 8_000_000


def _result(record: DocumentRecord, status: str | None = None) -> IngestionResult:
    return IngestionResult.model_validate({
        "document_id": record.document_id, "source": record.source,
        "status": status or record.last_attempt.status, "persisted": True,
        "active_version": record.active.version if record.active else None,
        "active_chunk_count": record.active.chunk_count if record.active else 0,
        "attempted_version": record.last_attempt.version,
        "warnings": record.last_attempt.warnings, "error": record.last_attempt.error,
    })


class KnowledgeBaseStore:
    """Trusted backend storage; future admin routes must authorize before calling."""

    def __init__(self, client: Client) -> None:
        self.client = client

    def _reference(self, source: str) -> DocumentReference:
        return self.client.collection("documents").document(encode_document_id(document_id_for_source(source)))

    def read(self, source: str, transaction: Transaction | None = None) -> DocumentRecord | None:
        with firestore_operation():
            snapshot = self._reference(source).get(transaction=transaction, timeout=FIRESTORE_READ_TIMEOUT)
        if not snapshot.exists:
            return None
        try:
            record = DocumentRecord.model_validate(snapshot.to_dict())
            if record.document_id != document_id_for_source(source):
                raise ValueError("Stored document identity mismatch.")
            return record
        except (ValidationError, ValueError) as exc:
            logger.error("Stored knowledge-base record failed validation for %s.", source)
            raise HTTPException(500, "Stored document metadata is invalid; contact the administrator.") from exc

    def read_active_chunks(self, source: str) -> list[DocumentChunk]:
        """Read the active manifest and its chunks in one consistent transaction."""
        def operation(transaction: Transaction) -> list[DocumentChunk]:
            record = self.read(source, transaction)
            if record is None:
                logger.warning("Active chunks requested for unknown document %s.", source)
                raise HTTPException(404, "Document not found.")
            return self._read_active_chunks(record, transaction)
        return run_transaction(self.client, operation)

    def read_index_snapshot(self) -> list[DocumentChunk]:
        """Read all active manifests and their chunks from a single Firestore snapshot."""
        def operation(transaction: Transaction) -> list[DocumentChunk]:
            snapshots = self.client.collection("documents").stream(
                transaction=transaction, timeout=FIRESTORE_READ_TIMEOUT,
            )
            chunks: list[DocumentChunk] = []
            for snapshot in snapshots:
                try:
                    record = DocumentRecord.model_validate(snapshot.to_dict())
                    if snapshot.id != encode_document_id(record.document_id):
                        raise ValueError("Document manifest key mismatch.")
                except ValueError as exc:
                    logger.error("Invalid document manifest encountered while reading index snapshot.")
                    raise HTTPException(500, "Stored document manifest is invalid.") from exc
                chunks.extend(self._read_active_chunks(record, transaction))
            return sorted(chunks, key=lambda chunk: chunk.chunk_id)
        return run_transaction(self.client, operation)

    def _read_active_chunks(self, record: DocumentRecord, transaction: Transaction) -> list[DocumentChunk]:
        if record.active is None:
            return []
        references = [
            self.client.collection("document_chunks").document(encode_document_id(key))
            for key in record.active.chunk_ids
        ]
        snapshots = self.client.get_all(references, transaction=transaction, timeout=FIRESTORE_READ_TIMEOUT)
        try:
            chunks = [DocumentChunk.model_validate(snapshot.to_dict()) for snapshot in snapshots]
            chunks.sort(key=lambda chunk: chunk.chunk_index)
            if ([chunk.chunk_id for chunk in chunks] != record.active.chunk_ids or any(
                chunk.document_id != record.document_id or chunk.version != record.active.version
                or chunk.chunk_index != index
                for index, chunk in enumerate(chunks)
            )):
                raise ValueError("Active chunks do not match the document manifest.")
            return chunks
        except (ValidationError, ValueError) as exc:
            logger.error("Active chunks failed validation for %s.", record.source)
            raise HTTPException(500, "Stored document chunks are invalid; contact the administrator.") from exc

    def save_attempt(
        self, source: str, expected_revision: str | None, attempt: ProcessingAttempt,
        chunks: list[DocumentChunk],
    ) -> IngestionResult:
        ids = [chunk.chunk_id for chunk in chunks]
        if attempt.status == "processed":
            if not chunks or len(set(ids)) != len(ids) or len(chunks) != attempt.chunk_count or any(
                chunk.document_id != document_id_for_source(source) or chunk.version != attempt.version
                or document_id_for_source(chunk.source) != document_id_for_source(source)
                or chunk.category != chunks[0].category or chunk.chunk_index != index
                for index, chunk in enumerate(chunks)
            ):
                logger.error("Prepared chunk identity/count mismatch for %s.", source)
                raise ValueError("Prepared chunks do not match the document attempt.")
        elif chunks:
            logger.error("Non-successful processing cannot publish chunks for %s.", source)
            raise ValueError("Only successfully processed documents can publish chunks.")

        revision = uuid4().hex
        now = utc_now()

        def operation(transaction: Transaction) -> IngestionResult:
            existing = self.read(source, transaction)
            active = existing.active if existing else None
            same_active = (
                attempt.status == "processed" and active is not None
                and active.version == attempt.version and active.chunk_ids == ids
                and active.chunking == attempt.chunking
            )
            if same_active and existing is not None:
                self._read_active_chunks(existing, transaction)
            # Concurrent identical publishes are idempotent, but stale different work cannot win.
            if same_active and existing is not None and existing.last_attempt.status == "processed":
                return _result(existing, "unchanged")
            if (existing.revision if existing else None) != expected_revision:
                logger.warning("Document changed during ingestion: %s.", source)
                raise HTTPException(409, "Document changed during processing. Reprocess it before retrying.")
            if attempt.status == "removed":
                if existing is None:
                    logger.warning("Cannot remove an unknown document: %s.", source)
                    raise HTTPException(404, "Document has not been published or recorded.")
                if existing.last_attempt.status == "removed":
                    return _result(existing, "unchanged")

            obsolete = []
            if attempt.status in {"processed", "removed"}:
                obsolete = [key for key in (active.chunk_ids if active else []) if key not in ids]
            if attempt.status == "processed":
                active = ActiveDocumentVersion(
                    version=chunks[0].version, category=chunks[0].category,
                    chunk_ids=ids, chunk_count=len(ids), chunking=attempt.chunking,
                    published_at=now,
                )
            elif attempt.status == "removed":
                active = None
            record = DocumentRecord(
                document_id=document_id_for_source(source), source=source,
                created_at=existing.created_at if existing else now,
                updated_at=now, revision=revision, active=active, last_attempt=attempt,
            )
            writes = [(self._reference(source), record.model_dump())]
            if not same_active:
                writes.extend((
                    self.client.collection("document_chunks").document(encode_document_id(chunk.chunk_id)),
                    chunk.model_dump(),
                ) for chunk in chunks)
            sizes = [Document(fields=encode_dict(payload))._pb.ByteSize() for _, payload in writes]
            if (len(writes) + len(obsolete) > MAX_TRANSACTION_WRITES
                    or max(sizes) > MAX_RECORD_BYTES or sum(sizes) > MAX_TRANSACTION_BYTES):
                logger.error("Atomic document transaction exceeds safe storage budgets: %s.", source)
                raise HTTPException(422, "Document exceeds atomic storage limits; reduce its size before publishing.")
            for reference, payload in writes:
                transaction.set(reference, payload)
            for key in obsolete:
                transaction.delete(self.client.collection("document_chunks").document(encode_document_id(key)))
            return _result(record)

        return run_transaction(self.client, operation)
