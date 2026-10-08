from collections.abc import Callable
from hashlib import sha256
import json
import logging
from pathlib import Path
from uuid import uuid4

import chromadb
from chromadb.config import Settings as ChromaSettings
from filelock import FileLock
from pydantic import BaseModel

from app.models.document_chunk import DocumentChunk
from app.rag.embeddings import Embedder
from app.rag.ingestion import write_json_atomic


logger = logging.getLogger(__name__)


class SearchHit(BaseModel):
    chunk: DocumentChunk
    score: float


class IndexManifest(BaseModel):
    collection: str
    fingerprint: dict
    content_hash: str
    count: int


def content_hash(chunks: list[DocumentChunk]) -> str:
    value = [chunk.model_dump(mode="json") for chunk in sorted(chunks, key=lambda item: item.chunk_id)]
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


class VectorIndex:
    """Publish complete generations, never expose a partly updated vector index."""

    def __init__(self, path: Path, embedder: Embedder, batch_size: int = 4):
        if batch_size < 1:
            logger.error("Index batch size must be positive.")
            raise ValueError("Invalid index batch size.")
        path.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.embedder = embedder
        self.batch_size = batch_size
        self.client = chromadb.PersistentClient(
            path=str(path), settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.lock = FileLock(str(path / "index.lock"), timeout=60)

    def _manifest(self) -> IndexManifest | None:
        path = self.path / "active_index.json"
        if not path.exists():
            return None
        try:
            manifest = IndexManifest.model_validate_json(path.read_text(encoding="utf-8"))
            if not manifest.collection.startswith("kq_generation_"):
                raise ValueError("Invalid collection ownership.")
            return manifest
        except ValueError:
            logger.error("Active vector-index manifest is invalid; restore it before indexing.")
            raise

    def _compatible(self, manifest: IndexManifest) -> bool:
        return manifest.fingerprint == self.embedder.fingerprint

    def sync(
        self, chunks: list[DocumentChunk], rebuild: bool = False,
        still_current: Callable[[], bool] | None = None,
    ) -> dict:
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            logger.error("Duplicate chunk IDs in index input.")
            raise ValueError("Index requires unique chunk IDs.")
        # Search segments may be smaller than a source chunk; every hit returns its complete parent.
        segments: list[DocumentChunk] = []
        parents: dict[str, DocumentChunk] = {}
        for chunk in chunks:
            texts = self.embedder.segments(chunk.text)
            if not texts or "".join(texts) != chunk.text:
                logger.error("Embedding segmentation did not preserve source text.")
                raise ValueError("Invalid embedding segmentation.")
            for index, text in enumerate(texts):
                identifier = chunk.chunk_id if len(texts) == 1 else sha256(
                    f"{chunk.chunk_id}:{index}:{text}".encode("utf-8")
                ).hexdigest()
                segments.append(chunk.model_copy(update={"chunk_id": identifier, "text": text}))
                parents[identifier] = chunk
        self.embedder.validate_passages(segments)
        digest = content_hash(chunks)
        with self.lock:
            previous = self._manifest()
            compatible = previous is not None and self._compatible(previous)
            if previous is not None and not compatible and not rebuild:
                logger.error("Embedding configuration changed; an explicit rebuild is required.")
                raise ValueError("Embedding configuration mismatch. Run index_documents.py --rebuild.")
            old = self.client.get_collection(previous.collection, embedding_function=None) if previous else None
            if old is not None and old.count() != previous.count:
                logger.error("Stored vector count does not match the published manifest.")
                raise ValueError("Incomplete vector index; restore storage before proceeding.")
            if previous and compatible and previous.content_hash == digest and not rebuild:
                if still_current is not None and not still_current():
                    logger.error("Published document snapshot changed during indexing.")
                    raise ValueError("Documents changed; retry indexing.")
                return {"status": "unchanged", "count": previous.count, "chunks": len(chunks),
                        "embedded": 0, "reused": previous.count, "removed": 0}

            # Reuse only exact payload matches; new/changed chunks alone require inference.
            saved = old.get(include=["embeddings", "metadatas"]) if old is not None else None
            reusable = {}
            if saved is not None and compatible and not rebuild:
                for index, identifier in enumerate(saved["ids"]):
                    reusable[identifier] = (saved["metadatas"][index]["payload"], saved["embeddings"][index].tolist())
            name = "kq_generation_" + uuid4().hex
            collection = self.client.create_collection(
                name, embedding_function=None, configuration={"hnsw": {"space": "cosine"}},
            )
            activated = False
            embedded = reused = 0
            try:
                for start in range(0, len(segments), self.batch_size):
                    batch = segments[start:start + self.batch_size]
                    payloads = [parents[c.chunk_id].model_dump_json() for c in batch]
                    missing = [c for c, payload in zip(batch, payloads)
                               if c.chunk_id not in reusable or reusable[c.chunk_id][0] != payload]
                    new_vectors = dict(zip([c.chunk_id for c in missing], self.embedder.embed_passages(missing), strict=True))
                    vectors = [new_vectors[c.chunk_id] if c.chunk_id in new_vectors else reusable[c.chunk_id][1]
                               for c in batch]
                    collection.add(
                        ids=[c.chunk_id for c in batch], embeddings=vectors,
                        documents=[c.text for c in batch],
                        metadatas=[{"payload": payload, "document_id": c.document_id, "version": c.version,
                                    "source": c.source, "category": c.category} for c, payload in zip(batch, payloads)],
                    )
                    embedded += len(missing)
                    reused += len(batch) - len(missing)
                if collection.count() != len(segments):
                    logger.error("Incomplete new vector generation.")
                    raise ValueError("Index verification failed.")
                if still_current is not None and not still_current():
                    logger.error("Published document snapshot changed during indexing.")
                    raise ValueError("Documents changed; retry indexing.")
                manifest = IndexManifest(
                    collection=name, fingerprint=self.embedder.fingerprint, content_hash=digest, count=len(segments),
                )
                # Atomic pointer replacement makes a restart see either the old or new snapshot.
                write_json_atomic(self.path / "active_index.json", manifest.model_dump())
                activated = True
            finally:
                if not activated:
                    logger.warning("Discarding unfinished vector generation; prior index remains active.")
                    self.client.delete_collection(name)
            old_ids = set(saved["ids"]) if saved else set()
            if previous is not None:
                self.client.delete_collection(previous.collection)
            return {
                "status": "indexed", "count": len(segments), "chunks": len(chunks),
                "embedded": embedded, "reused": reused,
                "removed": len(old_ids - {c.chunk_id for c in segments}),
            }

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        if not query.strip() or type(limit) is not int or limit < 1:
            logger.error("Search requires a nonempty question and positive integer result limit.")
            raise ValueError("Invalid search input.")
        with self.lock:
            manifest = self._manifest()
            if manifest is None:
                logger.error("No published vector index exists.")
                raise FileNotFoundError("Build the vector index before searching.")
            if not self._compatible(manifest):
                logger.error("Query embedding configuration does not match the stored index.")
                raise ValueError("Embedding configuration mismatch; rebuild the index.")
            collection = self.client.get_collection(manifest.collection, embedding_function=None)
            if collection.count() != manifest.count:
                logger.error("Active vector-index count mismatch.")
                raise ValueError("Stored vector index is incomplete.")
            if manifest.count == 0:
                return []
            vector = self.embedder.embed_query(query)
            results = collection.query(
                query_embeddings=[vector], n_results=manifest.count, include=["metadatas", "distances"],
            )
            hits = []
            seen: set[str] = set()
            for metadata, distance in zip(results["metadatas"][0], results["distances"][0], strict=True):
                chunk = DocumentChunk.model_validate_json(metadata["payload"])
                if chunk.chunk_id not in seen:
                    seen.add(chunk.chunk_id)
                    hits.append(SearchHit(chunk=chunk, score=1.0 - distance))
                    if len(hits) == limit:
                        break
            return hits
