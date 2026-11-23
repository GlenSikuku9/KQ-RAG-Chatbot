"""Compare the reviewed parent corpus with the active searchable segments.

This command is read-only. It distinguishes complete policy chunks from the
smaller embedding segments stored in Chroma and never publishes an index.
"""

import json
from pathlib import Path
import sys

from pydantic import TypeAdapter


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(BACKEND_DIR))

from app.models.document_chunk import DocumentChunk
from app.rag.retrieval import get_retrieval_service
from app.rag.vector_index import content_hash


DEFAULT_CORPUS = (
    BACKEND_DIR
    / "data"
    / "processed"
    / "kq_document_chunks.json"
)


def main() -> int:
    payload = json.loads(DEFAULT_CORPUS.read_text(encoding="utf-8"))
    reviewed = TypeAdapter(list[DocumentChunk]).validate_python(
        payload["chunks"],
    )
    reviewed_by_id = {
        chunk.chunk_id: chunk
        for chunk in reviewed
    }

    index = get_retrieval_service().index
    with index.active_lock:
        manifest = index._manifest()
        if manifest is None:
            raise RuntimeError("No active retrieval index exists.")
        collection = index.client.get_collection(
            manifest.collection,
            embedding_function=None,
        )
        stored = collection.get(include=["metadatas"])

    indexed_parents: dict[str, DocumentChunk] = {}
    segment_counts: dict[str, int] = {}
    payload_conflicts: set[str] = set()

    for metadata in stored["metadatas"]:
        parent = DocumentChunk.model_validate_json(metadata["payload"])
        previous = indexed_parents.get(parent.chunk_id)
        if previous is not None and previous != parent:
            payload_conflicts.add(parent.chunk_id)
        indexed_parents[parent.chunk_id] = parent
        segment_counts[parent.chunk_id] = (
            segment_counts.get(parent.chunk_id, 0) + 1
        )

    indexed_ids = set(indexed_parents)
    reviewed_ids = set(reviewed_by_id)
    changed_payloads = sorted(
        identifier
        for identifier in indexed_ids & reviewed_ids
        if indexed_parents[identifier] != reviewed_by_id[identifier]
    )
    changed_fields: dict[str, int] = {}
    for identifier in changed_payloads:
        reviewed_payload = reviewed_by_id[identifier].model_dump()
        indexed_payload = indexed_parents[identifier].model_dump()
        for field in reviewed_payload:
            if reviewed_payload[field] != indexed_payload[field]:
                changed_fields[field] = changed_fields.get(field, 0) + 1
    report = {
        "reviewed_parent_chunks": len(reviewed),
        "active_search_segments": len(stored["ids"]),
        "active_parent_chunks": len(indexed_parents),
        "segmented_parent_chunks": {
            identifier: count
            for identifier, count in segment_counts.items()
            if count > 1
        },
        "missing_from_index": sorted(reviewed_ids - indexed_ids),
        "stale_in_index": sorted(indexed_ids - reviewed_ids),
        "changed_parent_payload_count": len(changed_payloads),
        "changed_parent_fields": changed_fields,
        "changed_parent_payload_sample": changed_payloads[:5],
        "conflicting_segment_payloads": sorted(payload_conflicts),
        "reviewed_content_hash": content_hash(reviewed),
        "manifest_content_hash": manifest.content_hash,
        "content_hash_matches": (
            content_hash(reviewed) == manifest.content_hash
        ),
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
