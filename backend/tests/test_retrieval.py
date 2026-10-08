import json
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.config import Settings
from app.models.retrieval import RetrievalRequest
from app.rag.embeddings import EmbeddingInputError
from app.rag.retrieval import RetrievalService
from app.rag.vector_index import IndexStateError, SearchHit
from scripts import evaluate_retrieval, index_documents
from test_vector_index import chunk


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.index = Mock()
        self.index.search.return_value = [
            SearchHit(chunk=chunk(), score=0.85), SearchHit(chunk=chunk("second"), score=0.71),
        ]
        self.settings = Settings(retrieval_top_k=7)
        self.service = RetrievalService(self.index, self.settings)

    def test_original_question_language_and_normalized_embedding_query(self):
        original = "  Mizigo\t yangu\r\n allowance ni kiasi gani?  "
        result = self.service.retrieve(RetrievalRequest(question=original, language="mixed"))
        self.assertEqual(result.question, original)
        self.assertEqual(result.normalized_query, "Mizigo yangu allowance ni kiasi gani?")
        self.assertEqual(result.language, "mixed")
        self.assertEqual(result.language_source, "selected")
        self.index.search.assert_called_once_with(result.normalized_query, 7)

    def test_unspecified_language_is_unknown_without_guessing(self):
        for question in ("Hello", "Habari", "Hello habari"):
            result = self.service.retrieve(RetrievalRequest(question=question))
            self.assertEqual(result.language, "unknown")
            self.assertEqual(result.language_source, "unspecified")

    def test_invalid_input_rejected_before_search(self):
        for payload in (
            {"question": ""}, {"question": " \n\t"}, {"question": 12},
            {"question": "q" * 4001}, {"question": "q\x00"},
            {"question": "q\ud800"}, {"question": "q", "language": "fr"},
            {"question": "q", "top_k": 0}, {"question": "q", "top_k": 21},
            {"question": "q", "top_k": True}, {"question": "q", "top_k": "5"},
            {"question": "q", "unexpected": "field"},
        ):
            with self.subTest(payload=str(payload)[:80]):
                with self.assertRaises(ValidationError):
                    RetrievalRequest.model_validate(payload)
        self.index.search.assert_not_called()

    def test_result_ranks_scores_sources_and_effective_limit(self):
        result = self.service.retrieve(RetrievalRequest(question="Baggage?", top_k=2, language="en"))
        self.assertEqual(result.top_k, 2)
        self.assertEqual(result.returned_count, 2)
        self.assertEqual([hit.rank for hit in result.results], [1, 2])
        self.assertEqual(result.results[0].chunk, chunk())
        self.assertEqual(result.score_type, "cosine_similarity")
        self.assertEqual(result.evidence_status, "not_assessed")
        self.assertEqual(result.results[0].score, 0.85)
        self.assertGreaterEqual(result.retrieval_ms, 0)

    def test_empty_index_is_not_a_storage_error_or_answerability_claim(self):
        self.index.search.return_value = []
        result = self.service.retrieve(RetrievalRequest(question="Live flight status?"))
        self.assertEqual(result.results, [])
        self.assertEqual(result.returned_count, 0)
        self.assertEqual(result.evidence_status, "not_assessed")
        self.assertNotIn("answer", result.model_dump())

    def test_token_overflow_returns_explicit_input_error(self):
        self.index.search.side_effect = EmbeddingInputError("Question exceeds 512 tokens.")
        with self.assertLogs(level="WARNING"):
            with self.assertRaises(HTTPException) as caught:
                self.service.retrieve(RetrievalRequest(question="Many words"))
        self.assertEqual(caught.exception.status_code, 422)

    def test_storage_and_configuration_errors_are_sanitized_not_empty_results(self):
        for error in (FileNotFoundError("PRIVATE"), IndexStateError("PRIVATE"), OSError("PRIVATE")):
            self.index.search.side_effect = error
            with self.assertLogs(level="ERROR") as logs:
                with self.assertRaises(HTTPException) as caught:
                    self.service.retrieve(RetrievalRequest(question="Baggage?"))
            self.assertEqual(caught.exception.status_code, 503)
            self.assertNotIn("PRIVATE", caught.exception.detail + "\n".join(logs.output))

    def test_retrieval_default_limit_is_validated(self):
        for limit in (0, 21, True):
            with self.assertRaises(ValidationError):
                Settings(retrieval_top_k=limit)

    def test_cli_query_uses_same_service_and_returns_structured_response(self):
        response = self.service.retrieve(RetrievalRequest(question="Mizigo?", language="sw", top_k=3))
        with patch.object(index_documents, "get_retrieval_service") as provider, \
                patch.object(index_documents, "get_firestore_client") as firestore, \
                patch("builtins.print") as output:
            provider.return_value.retrieve.return_value = response
            self.assertEqual(index_documents.main(["--query", "Mizigo?", "--language", "sw", "--top-k", "3"]), 0)
        self.assertEqual(provider.return_value.retrieve.call_args.args[0].language, "sw")
        firestore.assert_not_called()
        data = json.loads(output.call_args.args[0])
        self.assertEqual(data["question"], "Mizigo?")
        self.assertEqual(data["score_type"], "cosine_similarity")

    def test_cli_invalid_input_does_not_initialize_retrieval(self):
        with patch.object(index_documents, "get_retrieval_service") as provider, self.assertLogs(level="ERROR"):
            self.assertEqual(index_documents.main(["--query", " ", "--top-k", "5"]), 1)
        provider.assert_not_called()

    def test_development_metrics_require_passage_evidence_not_just_source(self):
        case = {"id": "positive", "question": "bag?", "language": "en",
                "source": "policy.docx", "evidence": ["bag allowance"]}
        negative = {**case, "id": "negative", "source": None, "evidence": []}
        relevant = chunk()
        irrelevant = chunk("irrelevant", "refund only")
        self.index.search.return_value = [
            SearchHit(chunk=irrelevant, score=0.9), SearchHit(chunk=relevant, score=0.8),
        ]
        report = evaluate_retrieval.evaluate(self.service, [relevant, irrelevant], [case, negative], 2)
        self.assertEqual(report["metrics"]["all"], {
            "questions": 1, "recall_at_k": 1.0, "precision_at_k": 0.5, "reciprocal_rank_at_k": 0.5,
        })
        self.assertEqual(report["outcomes"][0]["first_relevant_rank"], 2)
        self.assertIsNone(report["outcomes"][1]["recall_at_k"])
        self.assertEqual(report["outcomes"][1]["evidence_status"], "not_assessed")

    def test_missing_development_evidence_is_an_error_not_a_negative_label(self):
        case = {"id": "missing", "question": "bag?", "language": "en",
                "source": "policy.docx", "evidence": ["NOT IN POLICY"]}
        with self.assertLogs(level="ERROR"):
            with self.assertRaises(ValueError):
                evaluate_retrieval.evaluate(self.service, [chunk()], [case], 5)
        self.index.search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
