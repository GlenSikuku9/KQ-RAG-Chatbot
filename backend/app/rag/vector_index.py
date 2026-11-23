from collections.abc import Callable
from collections import Counter
from hashlib import sha256
import json
import logging
from math import log
from pathlib import Path
import re
from uuid import uuid4

import chromadb
from chromadb.config import Settings as ChromaSettings
from filelock import FileLock
from pydantic import BaseModel, Field

from app.models.document_chunk import DocumentChunk
from app.rag.embeddings import Embedder
from app.rag.ingestion import write_json_atomic


logger = logging.getLogger(__name__)


class IndexStateError(ValueError):
    """Persistent index state is invalid or incompatible with the configured model."""


class SearchHit(BaseModel):
    chunk: DocumentChunk
    score: float = Field(allow_inf_nan=False)
    dense_rank: int | None = Field(default=None, ge=1)
    lexical_rank: int | None = Field(default=None, ge=1)
    fusion_score: float | None = Field(default=None, ge=0)


class IndexManifest(BaseModel):
    collection: str
    fingerprint: dict
    content_hash: str
    count: int


def _lexical_tokens(text: str) -> list[str]:
    """Return Unicode terms while retaining policy numbers and airport codes."""

    return re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE)


def _lexical_document(
    chunk: DocumentChunk,
    *,
    include_taxonomy: bool = True,
) -> str:
    """Weight source taxonomy and headings without question-specific rules."""

    category = chunk.category.strip()
    section = (chunk.section or "").strip()
    fields = [chunk.text]
    if include_taxonomy:
        fields = [
            category,
            category,
            category,
            section,
            section,
            *fields,
        ]
    return "\n".join(field for field in fields if field)


def _bm25_scores(
    query: str,
    chunks: list[DocumentChunk],
    *,
    include_taxonomy: bool = True,
    include_heading_signal: bool = True,
) -> list[float]:
    """Calculate BM25 with a lightweight heading-specificity signal."""

    query_terms = list(dict.fromkeys(_lexical_tokens(query)))
    documents = [
        _lexical_tokens(_lexical_document(
            chunk,
            include_taxonomy=include_taxonomy,
        ))
        for chunk in chunks
    ]
    if not query_terms or not documents:
        return [0.0 for _chunk in chunks]

    document_count = len(documents)
    average_length = sum(map(len, documents)) / document_count
    document_frequency = {
        term: sum(term in document for document in documents)
        for term in query_terms
    }
    k1 = 1.5
    b = 0.75
    scores: list[float] = []

    for document in documents:
        frequencies = Counter(document)
        length_normalization = 1 - b + b * len(document) / average_length
        score = 0.0

        for term in query_terms:
            frequency = frequencies[term]
            if frequency == 0:
                continue
            frequency_in_documents = document_frequency[term]
            inverse_document_frequency = log(
                1
                + (
                    document_count
                    - frequency_in_documents
                    + 0.5
                )
                / (frequency_in_documents + 0.5),
            )
            score += inverse_document_frequency * (
                frequency * (k1 + 1)
                / (frequency + k1 * length_normalization)
            )

        scores.append(score)

    # A document title or section that repeats the passenger's terminology is
    # more specific than body text that mentions the same words incidentally.
    # This applies uniformly to every policy and does not encode any answers.
    if not include_heading_signal:
        return scores

    maximum_bm25 = max(scores, default=0.0)
    query_term_set = set(query_terms)
    query_bigrams = {
        (query_terms[index], query_terms[index + 1])
        for index in range(len(query_terms) - 1)
    }
    adjusted: list[float] = []

    for chunk, score in zip(chunks, scores, strict=True):
        heading_tokens = _lexical_tokens(
            " ".join((chunk.category, chunk.section or "")),
        )
        heading_set = set(heading_tokens)
        heading_coverage = (
            len(query_term_set & heading_set) / len(query_term_set)
        )
        heading_bigrams = {
            (heading_tokens[index], heading_tokens[index + 1])
            for index in range(len(heading_tokens) - 1)
        }
        phrase_matches = len(query_bigrams & heading_bigrams)
        heading_weight = maximum_bm25 or 1.0
        adjusted.append(
            score
            + heading_weight * heading_coverage
            + heading_weight * phrase_matches
        )

    return adjusted


def content_hash(chunks: list[DocumentChunk]) -> str:
    value = [chunk.model_dump(mode="json") for chunk in sorted(chunks, key=lambda item: item.chunk_id)]
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def representation_hash(
    chunks: list[DocumentChunk],
    search_representations: dict[str, list[str]] | None,
) -> str:
    """Fingerprint authoritative parents plus optional search-only text."""

    value = {
        "parents": [
            chunk.model_dump(mode="json")
            for chunk in sorted(chunks, key=lambda item: item.chunk_id)
        ],
        "search_representations": search_representations,
    }
    return sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8"),
    ).hexdigest()


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
        # Writers serialize full rebuilds separately. Passenger searches keep
        # reading the current immutable generation while the next one embeds.
        self.write_lock = FileLock(str(path / "index-write.lock"), timeout=60)
        self.active_lock = FileLock(str(path / "index.lock"), timeout=60)
        # The active generation is immutable, so its small lexical corpus and
        # vectors can be reused until a new manifest is published.
        self._hybrid_snapshot: tuple[
            str,
            list[dict],
            list[list[float]],
        ] | None = None

    def _manifest(self) -> IndexManifest | None:
        path = self.path / "active_index.json"
        if not path.exists():
            return None
        try:
            manifest = IndexManifest.model_validate_json(path.read_text(encoding="utf-8"))
            if not manifest.collection.startswith("kq_generation_"):
                raise ValueError("Invalid collection ownership.")
            return manifest
        except ValueError as exc:
            logger.error("Active vector-index manifest is invalid; restore it before indexing.")
            raise IndexStateError("Invalid vector-index manifest.") from exc

    def _compatible(self, manifest: IndexManifest) -> bool:
        return manifest.fingerprint == self.embedder.fingerprint

    def sync(
        self, chunks: list[DocumentChunk], rebuild: bool = False,
        still_current: Callable[[], bool] | None = None,
        search_representations: dict[str, list[str]] | None = None,
    ) -> dict:
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            logger.error("Duplicate chunk IDs in index input.")
            raise ValueError("Index requires unique chunk IDs.")
        chunk_ids = {chunk.chunk_id for chunk in chunks}
        if search_representations is not None and set(search_representations) != chunk_ids:
            logger.error("Search representations must cover every parent exactly once.")
            raise ValueError("Invalid search representations.")
        # Search segments may be smaller than a source chunk; every hit returns its complete parent.
        segments: list[DocumentChunk] = []
        parents: dict[str, DocumentChunk] = {}
        for chunk in chunks:
            representations = (
                search_representations[chunk.chunk_id]
                if search_representations is not None
                else [chunk.text]
            )
            if not representations or any(not text.strip() for text in representations):
                logger.error("Search representations must be nonempty.")
                raise ValueError("Invalid search representations.")
            texts: list[str] = []
            for representation in representations:
                parts = self.embedder.segments(representation)
                if not parts or "".join(parts) != representation:
                    logger.error("Embedding segmentation did not preserve search text.")
                    raise ValueError("Invalid embedding segmentation.")
                texts.extend(parts)
            for index, text in enumerate(texts):
                identifier = (
                    chunk.chunk_id
                    if search_representations is None and len(texts) == 1
                    else sha256(
                        f"{chunk.chunk_id}:{index}:{text}".encode("utf-8"),
                    ).hexdigest()
                )
                segments.append(chunk.model_copy(update={"chunk_id": identifier, "text": text}))
                parents[identifier] = chunk
        self.embedder.validate_passages(segments)
        digest = (
            content_hash(chunks)
            if search_representations is None
            else representation_hash(chunks, search_representations)
        )
        with self.write_lock:
            previous = self._manifest()
            compatible = previous is not None and self._compatible(previous)
            if previous is not None and not compatible and not rebuild:
                logger.error("Embedding configuration changed; an explicit rebuild is required.")
                raise IndexStateError("Embedding configuration mismatch. Run index_documents.py --rebuild.")
            old = self.client.get_collection(previous.collection, embedding_function=None) if previous else None
            if old is not None and old.count() != previous.count:
                logger.error("Stored vector count does not match the published manifest.")
                raise IndexStateError("Incomplete vector index; restore storage before proceeding.")
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
                        metadatas=[
                            {
                                "payload": payload,
                                "document_id": c.document_id,
                                "version": c.version,
                                "source": c.source,
                                "category": c.category,
                                # Search-only text can be translated or contextualized.
                                # The payload remains the authoritative parent.
                                "search_text": c.text,
                            }
                            for c, payload in zip(
                                batch,
                                payloads,
                                strict=True,
                            )
                        ],
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
                # Block searches only for the short atomic publication step.
                # A search that started earlier finishes against the old
                # immutable collection before that collection is removed.
                with self.active_lock:
                    current = self._manifest()
                    if current != previous:
                        raise ValueError("Active index changed during indexing; retry publication.")
                    write_json_atomic(
                        self.path / "active_index.json",
                        manifest.model_dump(),
                    )
                    self._hybrid_snapshot = None
                    activated = True
                    if previous is not None:
                        try:
                            self.client.delete_collection(previous.collection)
                        except Exception:
                            # Publication already succeeded. Retaining an
                            # unreferenced old collection is safer than rolling
                            # back or deleting the newly active generation.
                            logger.exception(
                                "Could not remove the superseded index collection."
                            )
            finally:
                if not activated:
                    logger.warning("Discarding unfinished vector generation; prior index remains active.")
                    self.client.delete_collection(name)
            old_ids = set(saved["ids"]) if saved else set()
            return {
                "status": "indexed", "count": len(segments), "chunks": len(chunks),
                "embedded": embedded, "reused": reused,
                "removed": len(old_ids - {c.chunk_id for c in segments}),
            }

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        if not query.strip() or type(limit) is not int or limit < 1:
            logger.error("Search requires a nonempty question and positive integer result limit.")
            raise ValueError("Invalid search input.")

        # Model initialization can take longer than the index lock timeout on
        # the first request. It does not access Chroma, so complete it before
        # taking the short lock that protects the published collection.
        fingerprint = self.embedder.fingerprint
        vector = self.embedder.embed_query(query)
        with self.active_lock:
            manifest = self._manifest()
            if manifest is None:
                logger.error("No published vector index exists.")
                raise FileNotFoundError("Build the vector index before searching.")
            if manifest.fingerprint != fingerprint:
                logger.error("Query embedding configuration does not match the stored index.")
                raise IndexStateError("Embedding configuration mismatch; rebuild the index.")
            collection = self.client.get_collection(manifest.collection, embedding_function=None)
            if collection.count() != manifest.count:
                logger.error("Active vector-index count mismatch.")
                raise IndexStateError("Stored vector index is incomplete.")
            if manifest.count == 0:
                return []
            # Oversample progressively so multiple segments of one parent do not crowd out other chunks.
            candidates = min(manifest.count, limit * 2)
            while True:
                results = collection.query(
                    query_embeddings=[vector], n_results=candidates, include=["metadatas", "distances"],
                )
                hits: dict[str, SearchHit] = {}
                try:
                    metadatas = results["metadatas"][0]
                    distances = results["distances"][0]
                    if len(metadatas) != candidates or len(distances) != candidates:
                        raise ValueError("Incomplete retrieval response.")
                    for metadata, distance in zip(metadatas, distances, strict=True):
                        chunk = DocumentChunk.model_validate_json(metadata["payload"])
                        hit = SearchHit(chunk=chunk, score=1.0 - distance)
                        if chunk.chunk_id not in hits or hit.score > hits[chunk.chunk_id].score:
                            hits[chunk.chunk_id] = hit
                except (ValueError, KeyError, TypeError, IndexError) as exc:
                    logger.error("Invalid stored retrieval payload or score.")
                    raise IndexStateError("Stored retrieval payload is invalid.") from exc
                ranked = sorted(hits.values(), key=lambda hit: (-hit.score, hit.chunk.chunk_id))
                if len(ranked) >= limit or candidates == manifest.count:
                    return ranked[:limit]
                candidates = min(manifest.count, candidates * 2)

    def search_hybrid(
        self,
        query: str,
        limit: int,
        *,
        dense_candidates: int,
        lexical_candidates: int,
        rrf_k: int,
    ) -> list[SearchHit]:
        """Fuse exact dense and BM25 ranks while preserving cosine scores."""

        values = (limit, dense_candidates, lexical_candidates, rrf_k)
        if (
            not query.strip()
            or any(type(value) is not int or value < 1 for value in values)
        ):
            logger.error("Hybrid search received invalid input.")
            raise ValueError("Invalid hybrid-search input.")

        # Only one query embedding is generated. For this small policy corpus,
        # an exact dot-product scan is both lightweight and deterministic.
        fingerprint = self.embedder.fingerprint
        query_vector = self.embedder.embed_query(query)

        with self.active_lock:
            manifest = self._manifest()
            if manifest is None:
                logger.error("No published vector index exists.")
                raise FileNotFoundError(
                    "Build the vector index before searching.",
                )
            if manifest.fingerprint != fingerprint:
                logger.error(
                    "Query embedding configuration does not match the stored index.",
                )
                raise IndexStateError(
                    "Embedding configuration mismatch; rebuild the index.",
                )
            if manifest.count == 0:
                return []

            collection = self.client.get_collection(
                manifest.collection,
                embedding_function=None,
            )
            if collection.count() != manifest.count:
                logger.error("Active vector-index count mismatch.")
                raise IndexStateError("Stored vector index is incomplete.")

            snapshot = self._hybrid_snapshot
            if snapshot is None or snapshot[0] != manifest.collection:
                stored = collection.get(
                    include=["embeddings", "metadatas"],
                )
                try:
                    metadatas = stored["metadatas"]
                    embeddings = stored["embeddings"]
                    if (
                        metadatas is None
                        or embeddings is None
                        or len(metadatas) != manifest.count
                        or len(embeddings) != manifest.count
                    ):
                        raise ValueError("Incomplete hybrid snapshot.")
                    vectors = [
                        vector.tolist()
                        if hasattr(vector, "tolist")
                        else list(vector)
                        for vector in embeddings
                    ]
                except (KeyError, TypeError, ValueError) as exc:
                    logger.error("Stored hybrid-retrieval data is invalid.")
                    raise IndexStateError(
                        "Stored retrieval payload is invalid.",
                    ) from exc
                snapshot = (
                    manifest.collection,
                    metadatas,
                    vectors,
                )
                self._hybrid_snapshot = snapshot

        parent_chunks: dict[str, DocumentChunk] = {}
        dense_scores: dict[str, float] = {}
        lexical_representations: list[DocumentChunk] = []
        lexical_parent_ids: list[str] = []

        for metadata, vector in zip(
            snapshot[1],
            snapshot[2],
            strict=True,
        ):
            try:
                chunk = DocumentChunk.model_validate_json(
                    metadata["payload"],
                )
                similarity = sum(
                    query_value * passage_value
                    for query_value, passage_value in zip(
                        query_vector,
                        vector,
                        strict=True,
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                logger.error("Stored hybrid-retrieval payload is invalid.")
                raise IndexStateError(
                    "Stored retrieval payload is invalid.",
                ) from exc

            parent_chunks[chunk.chunk_id] = chunk
            dense_scores[chunk.chunk_id] = max(
                similarity,
                dense_scores.get(chunk.chunk_id, float("-inf")),
            )
            search_text = metadata.get("search_text", chunk.text)
            lexical_representations.append(
                chunk.model_copy(update={"text": search_text}),
            )
            lexical_parent_ids.append(chunk.chunk_id)

        chunks = sorted(
            parent_chunks.values(),
            key=lambda chunk: chunk.chunk_id,
        )
        dense_order = sorted(
            chunks,
            key=lambda chunk: (
                -dense_scores[chunk.chunk_id],
                chunk.chunk_id,
            ),
        )[:dense_candidates]
        # Score each representation independently, then retain the best score
        # per parent. Multiple translations therefore cannot accumulate score.
        lexical_scores = {
            chunk.chunk_id: 0.0
            for chunk in chunks
        }
        representation_scores = _bm25_scores(
            query,
            lexical_representations,
        )
        for parent_id, score in zip(
            lexical_parent_ids,
            representation_scores,
            strict=True,
        ):
            lexical_scores[parent_id] = max(
                lexical_scores[parent_id],
                score,
            )
        lexical_order = [
            chunk
            for chunk in sorted(
                chunks,
                key=lambda chunk: (
                    -lexical_scores[chunk.chunk_id],
                    chunk.chunk_id,
                ),
            )
            if lexical_scores[chunk.chunk_id] > 0
        ][:lexical_candidates]

        dense_ranks = {
            chunk.chunk_id: rank
            for rank, chunk in enumerate(dense_order, 1)
        }
        lexical_ranks = {
            chunk.chunk_id: rank
            for rank, chunk in enumerate(lexical_order, 1)
        }
        candidate_ids = set(dense_ranks) | set(lexical_ranks)

        def fusion_score(identifier: str) -> float:
            score = 0.0
            if identifier in dense_ranks:
                score += 1 / (rrf_k + dense_ranks[identifier])
            if identifier in lexical_ranks:
                score += 1 / (rrf_k + lexical_ranks[identifier])
            return score

        ranked_ids = sorted(
            candidate_ids,
            key=lambda identifier: (
                -fusion_score(identifier),
                -dense_scores[identifier],
                identifier,
            ),
        )[:limit]

        return [
            SearchHit(
                chunk=parent_chunks[identifier],
                score=dense_scores[identifier],
                dense_rank=dense_ranks.get(identifier),
                lexical_rank=lexical_ranks.get(identifier),
                fusion_score=fusion_score(identifier),
            )
            for identifier in ranked_ids
        ]
