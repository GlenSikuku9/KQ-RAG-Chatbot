import unittest
from unittest.mock import Mock, patch

import numpy as np

from app.config import Settings
from app.rag.embeddings import E5Embedder
from test_vector_index import chunk


class EmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(embedding_model_name="test/model", embedding_model_revision="a" * 40)
        self.model = Mock()
        self.model.max_seq_length = 512
        self.model.get_embedding_dimension.return_value = 3
        self.model.tokenizer.side_effect = lambda text, **kwargs: {"input_ids": list(range(len(text.split()) + 2))}
        self.model.encode.return_value = np.array([[1.0, 0.0, 0.0]])
        self.loader = patch("app.rag.embeddings.load_model", return_value=self.model).start()
        self.addCleanup(patch.stopall)
        self.embedder = E5Embedder(self.settings)

    def test_passage_and_query_prefixes_normalized_cpu_batches(self):
        self.embedder.embed_passages([chunk()])
        self.assertEqual(self.model.encode.call_args.args[0], ["passage: bag allowance"])
        self.assertTrue(self.model.encode.call_args.kwargs["normalize_embeddings"])
        self.embedder.embed_query("mizigo yangu")
        self.assertEqual(self.model.encode.call_args.args[0], ["query: mizigo yangu"])
        self.loader.assert_called_once()

    def test_token_limit_includes_prefix_and_special_tokens(self):
        self.model.tokenizer.return_value = {"input_ids": list(range(513))}
        self.model.tokenizer.side_effect = None
        with self.assertLogs(level="ERROR"):
            with self.assertRaises(ValueError):
                self.embedder.embed_passages([chunk()])
        self.model.encode.assert_not_called()
        self.assertFalse(self.model.tokenizer.call_args.kwargs["truncation"])

    def test_exact_limit_is_allowed(self):
        self.model.tokenizer.side_effect = None
        self.model.tokenizer.return_value = {"input_ids": list(range(512))}
        self.assertEqual(self.embedder.embed_query("query"), [1.0, 0.0, 0.0])

    def test_oversized_segments_preserve_every_character_without_truncation(self):
        text = "A policy word. " * 400
        segments = self.embedder.segments(text)
        self.assertGreater(len(segments), 1)
        self.assertEqual("".join(segments), text)
        for segment in segments:
            self.assertLessEqual(len(("passage: " + segment).split()) + 2, 512)

    def test_invalid_model_output_rejected(self):
        for value in (np.array([[float("nan"), 0, 0]]), np.array([[0, 0, 0]]),
                      np.array([[1, 0]])):
            self.model.encode.return_value = value
            with self.assertLogs(level="ERROR"):
                with self.assertRaises(ValueError):
                    self.embedder.embed_query("question")

    def test_missing_configuration_does_not_download_model(self):
        with self.assertLogs(level="ERROR"):
            with self.assertRaises(ValueError):
                E5Embedder(Settings()).embed_query("question")
        self.loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
