from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.models.document_chunk import DocumentChunk
from app.rag.vector_index import VectorIndex


class FakeEmbedder:
    fingerprint = {"model": "test", "revision": "one", "dimension": 3, "normalization": True}

    def __init__(self):
        self.calls = []

    def validate_passages(self, chunks):
        for chunk in chunks:
            if chunk.text == "TOO LONG":
                raise ValueError("Token limit exceeded.")

    def segments(self, text):
        return [text]

    def embed_passages(self, chunks):
        self.calls.extend(chunk.chunk_id for chunk in chunks)
        return [[1.0, 0.0, 0.0] if "bag" in chunk.text else [0.0, 1.0, 0.0] for chunk in chunks]

    def embed_query(self, text):
        if not text.strip():
            raise ValueError("Empty query")
        return [1.0, 0.0, 0.0]


def chunk(identifier="one", text="bag allowance", version="v1"):
    return DocumentChunk(
        chunk_id=identifier, document_id="doc", version=version, source="policy.docx",
        file_type="docx", category="policy", chunk_index=0, text=text,
        source_locations=["Body/block 1"],
    )


class VectorIndexTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.embedder = FakeEmbedder()
        self.index = VectorIndex(self.path, self.embedder, batch_size=2)
        self.addCleanup(self.index.client.close)

    def test_persistence_and_unchanged_sync_reuse_embeddings(self):
        self.assertEqual(self.index.sync([chunk()])["embedded"], 1)
        self.assertEqual(self.index.sync([chunk()])["status"], "unchanged")
        reopened = VectorIndex(self.path, self.embedder, batch_size=2)
        self.addCleanup(reopened.client.close)
        found = reopened.search("bag", 5)
        self.assertEqual(found[0].chunk, chunk())
        self.assertAlmostEqual(found[0].score, 1.0)
        self.assertEqual(self.embedder.calls, ["one"])

    def test_changed_snapshot_reuses_unchanged_and_removes_obsolete(self):
        self.index.sync([chunk(), chunk("old", "refund", "old")])
        result = self.index.sync([chunk(), chunk("new", "new bag", "new")])
        self.assertEqual(result["embedded"], 1)
        self.assertEqual(result["reused"], 1)
        self.assertEqual(result["removed"], 1)
        self.assertEqual({hit.chunk.chunk_id for hit in self.index.search("bag", 10)}, {"one", "new"})

    def test_changed_configuration_requires_explicit_rebuild(self):
        self.index.sync([chunk()])
        self.embedder.fingerprint = {**self.embedder.fingerprint, "revision": "two"}
        with self.assertLogs(level="ERROR"):
            with self.assertRaises(ValueError):
                self.index.sync([chunk()])
        with self.assertLogs(level="ERROR"):
            with self.assertRaises(ValueError):
                self.index.search("bag", 1)
        self.assertEqual(self.index.sync([chunk()], rebuild=True)["embedded"], 1)
        self.assertEqual(len(self.index.search("bag", 1)), 1)

    def test_failed_embedding_keeps_previous_generation(self):
        self.index.sync([chunk()])
        with patch.object(self.embedder, "embed_passages", side_effect=RuntimeError("inference failed")):
            with self.assertRaises(RuntimeError):
                self.index.sync([chunk("new")])
        self.assertEqual(self.index.search("bag", 1)[0].chunk.chunk_id, "one")

    def test_source_changed_during_build_does_not_activate(self):
        self.index.sync([chunk()])
        with self.assertLogs(level="ERROR"):
            with self.assertRaises(ValueError):
                self.index.sync([chunk("new")], still_current=lambda: False)
        self.assertEqual(self.index.search("bag", 1)[0].chunk.chunk_id, "one")

    def test_empty_snapshot_removes_all_vectors_without_embedding(self):
        self.index.sync([chunk()])
        result = self.index.sync([])
        self.assertEqual(result["removed"], 1)
        self.assertEqual(self.index.search("bag", 5), [])
        self.assertEqual(self.embedder.calls, ["one"])

    def test_oversize_and_duplicate_input_do_not_replace_index(self):
        self.index.sync([chunk()])
        with self.assertRaises(ValueError):
            self.index.sync([chunk("long", "TOO LONG")])
        with self.assertLogs(level="ERROR"):
            with self.assertRaises(ValueError):
                self.index.sync([chunk(), chunk()])
        self.assertEqual(self.index.search("bag", 1)[0].chunk.chunk_id, "one")

    def test_same_id_with_changed_payload_is_not_reused(self):
        self.index.sync([chunk()])
        result = self.index.sync([chunk(text="refund")])
        self.assertEqual(result["embedded"], 1)
        self.assertEqual(self.index.search("bag", 1)[0].chunk.text, "refund")

    def test_segment_vectors_return_complete_parent_once_and_reuse_on_restart(self):
        parent = chunk(text="bag allowance and refund")
        with patch.object(self.embedder, "segments", return_value=["bag allowance ", "and refund"]):
            result = self.index.sync([parent])
            self.assertEqual(result["count"], 2)
            self.assertEqual(result["chunks"], 1)
            self.assertEqual(self.index.sync([parent])["embedded"], 0)
        hits = self.index.search("bag", 5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].chunk, parent)

    def test_invalid_queries_and_missing_manifest_are_explicit(self):
        with self.assertLogs(level="ERROR"):
            with self.assertRaises(ValueError):
                self.index.search("bag", 0)
        with self.assertLogs(level="ERROR"):
            with self.assertRaises(FileNotFoundError):
                self.index.search("bag", 5)
        self.index.sync([chunk()])
        with self.assertLogs(level="ERROR"):
            with self.assertRaises(ValueError):
                self.index.search(" ", 5)

    def test_corrupt_manifest_is_not_treated_as_empty_index(self):
        (self.path / "active_index.json").write_text("{}")
        with self.assertLogs(level="ERROR"):
            with self.assertRaises(ValueError):
                self.index.sync([chunk()])


if __name__ == "__main__":
    unittest.main()
