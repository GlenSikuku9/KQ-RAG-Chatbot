import os
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.models.retrieval import RankedChunk, RetrievalRequest, RetrievalResponse
from app.rag.context import ContextService, build_context
from scripts.evaluate_retrieval import evaluate
from test_vector_index import chunk


def retrieved(*items, language="en"):
    return RetrievalResponse(
        question="  Baggage allowance?  ", normalized_query="Baggage allowance?",
        language=language, language_source="unspecified" if language == "unknown" else "selected",
        top_k=5, returned_count=len(items),
        results=[RankedChunk(rank=i, chunk=item, score=score)
                 for i, (item, score) in enumerate(items, 1)], retrieval_ms=1,
    )


class ContextTests(unittest.TestCase):
    def test_complete_cited_context_and_original_question_are_preserved(self):
        original = chunk(text="Baggage policy.\nRestrictions also apply.")
        response = retrieved((original, 0.9))
        result = build_context(response, Settings())
        self.assertEqual(result.context, "[S1]\n" + original.text)
        self.assertEqual(result.context_chars, len(result.context))
        self.assertEqual(result.sources[0].chunk, original)
        self.assertEqual(result.sources[0].citation_id, "S1")
        self.assertEqual(result.sources[0].retrieval_rank, 1)
        self.assertEqual(result.retrieval.question, response.question)
        self.assertEqual(result.decision, "context_available")
        self.assertEqual(result.evidence_status, "heuristic_only")
        self.assertFalse(result.generation_called)
        self.assertIsNone(result.message)

    def test_each_chunk_must_meet_threshold_including_exact_boundary(self):
        response = retrieved((chunk("high"), 0.9), (chunk("low", "refund"), 0.8699),
                             (chunk("boundary", "check-in"), 0.87))
        result = build_context(response, Settings())
        self.assertEqual([source.chunk.chunk_id for source in result.sources], ["high", "boundary"])
        self.assertEqual(result.excluded[0].chunk_id, "low")
        self.assertEqual(result.excluded[0].reason, "below_similarity_threshold")
        self.assertEqual(result.sources[1].citation_id, "S2")
        self.assertEqual(result.sources[1].retrieval_rank, 3)

    def test_budget_counts_citation_labels_and_separators_exactly(self):
        first, second = chunk(), chunk("two", "refund policy")
        expected = "[S1]\n" + first.text + "\n\n[S2]\n" + second.text
        response = retrieved((first, 0.95), (second, 0.9))
        result = build_context(response, Settings(context_max_chars=len(expected)))
        self.assertEqual(result.context, expected)
        result = build_context(response, Settings(context_max_chars=len(expected) - 1))
        self.assertEqual(len(result.sources), 1)
        self.assertEqual(result.excluded[0].reason, "context_budget_exceeded")
        self.assertLessEqual(result.context_chars, result.max_context_chars)

    def test_oversized_chunk_is_not_truncated_and_smaller_later_chunk_can_fit(self):
        response = retrieved((chunk("large", "a" * 1000), 0.95), (chunk("small", "policy"), 0.9))
        result = build_context(response, Settings(context_max_chars=100))
        self.assertEqual(result.context, "[S1]\npolicy")
        self.assertEqual(result.sources[0].retrieval_rank, 2)
        self.assertEqual(result.excluded[0].reason, "context_budget_exceeded")

    def test_table_rows_and_qualifications_are_kept_whole(self):
        table = chunk(text="Table:\nRow 1: Route | Allowance\nRow 2: Europe | 32kg\nConditions apply.")
        table.table_location = "Body/block 1"
        table.table_rows = [1, 2]
        response = retrieved((table, 0.95))
        result = build_context(response, Settings(context_max_chars=1000))
        self.assertEqual(result.sources[0].chunk, table)
        with self.assertLogs(level="WARNING"):
            result = build_context(response, Settings(context_max_chars=20))
        self.assertEqual(result.context, "")
        self.assertEqual(result.decision, "fallback")
        self.assertEqual(result.reason, "context_budget_exceeded")

    def test_duplicate_or_contained_complete_chunks_are_omitted_with_traceability(self):
        response = retrieved((chunk("first", "full baggage policy"), 0.99),
                             (chunk("copy", "full baggage policy"), 0.95),
                             (chunk("contained", "baggage policy"), 0.9))
        result = build_context(response, Settings())
        self.assertEqual(len(result.sources), 1)
        self.assertEqual([item.reason for item in result.excluded], ["duplicate_content"] * 2)
        self.assertEqual([item.duplicate_of for item in result.excluded], ["S1"] * 2)

    def test_partial_overlap_is_retained_to_avoid_removing_qualifications(self):
        response = retrieved((chunk("one", "First rule.\nShared text."), 0.95),
                             (chunk("two", "Shared text.\nException to the rule."), 0.9))
        result = build_context(response, Settings())
        self.assertEqual(len(result.sources), 2)
        self.assertIn("Exception to the rule.", result.context)

    def test_empty_results_fall_back_and_low_scores_request_clarification(self):
        empty = build_context(retrieved(), Settings())
        self.assertEqual(empty.decision, "fallback")
        self.assertEqual(empty.reason, "no_retrieved_chunks")
        low = build_context(retrieved((chunk(), 0.86)), Settings())
        self.assertEqual(low.decision, "clarification_required")
        self.assertEqual(low.reason, "below_similarity_threshold")
        for result in (empty, low):
            self.assertEqual(result.context, "")
            self.assertEqual(result.sources, [])
            self.assertFalse(result.generation_called)
            self.assertTrue(result.message)

    def test_selected_language_controls_static_message_unknown_does_not_guess(self):
        messages = {}
        for language in ("en", "sw", "mixed", "unknown"):
            result = build_context(retrieved(language=language), Settings())
            messages[language] = result.message
            self.assertEqual(result.retrieval.language, language)
        self.assertEqual(messages["unknown"], messages["en"])
        self.assertNotEqual(messages["en"], messages["sw"])
        self.assertNotEqual(messages["en"], messages["mixed"])

    def test_invalid_settings_fail_explicitly(self):
        for values in (
            {"context_max_chars": 0}, {"context_max_chars": True}, {"context_max_chars": 100001},
            {"context_min_similarity": -1.1}, {"context_min_similarity": 1.1},
            {"context_min_similarity": float("nan")}, {"context_min_similarity": True},
        ):
            with self.assertRaises(ValidationError):
                Settings(**values)

    def test_service_reuses_retrieval_and_propagates_unavailability_without_fallback(self):
        retrieval = Mock()
        retrieval.retrieve.return_value = retrieved((chunk(), 0.9))
        service = ContextService(retrieval, Settings())
        request = RetrievalRequest(question="Baggage?")
        self.assertEqual(service.prepare(request).decision, "context_available")
        retrieval.retrieve.assert_called_once_with(request)
        retrieval.retrieve.side_effect = HTTPException(503, "Retrieval unavailable.")
        with self.assertRaises(HTTPException) as caught:
            service.prepare(request)
        self.assertEqual(caught.exception.status_code, 503)

    def test_settings_are_loaded_from_environment_and_threshold_is_configurable(self):
        self.addCleanup(get_settings.cache_clear)
        with patch.dict(os.environ, {"CONTEXT_MAX_CHARS": "4567", "CONTEXT_MIN_SIMILARITY": "0.8"}):
            get_settings.cache_clear()
            settings = get_settings()
        self.assertEqual(settings.context_max_chars, 4567)
        self.assertEqual(settings.context_min_similarity, 0.8)
        result = build_context(retrieved((chunk(), 0.85)), settings)
        self.assertEqual(result.decision, "context_available")

    def test_identical_content_in_other_source_versions_keeps_distinct_citations(self):
        first = chunk()
        other_version = chunk("two", version="v2")
        other_document = chunk("three")
        other_document.document_id = "another-document"
        response = retrieved((first, 0.95), (other_version, 0.9), (other_document, 0.88))
        self.assertEqual(len(build_context(response, Settings()).sources), 3)

    def test_identical_text_in_different_policy_locations_is_not_collapsed(self):
        first = chunk()
        another_location = chunk("two")
        another_location.source_locations = ["Body/block 2"]
        another_section = chunk("three")
        another_section.section = "Different conditions"
        response = retrieved((first, 0.95), (another_location, 0.9), (another_section, 0.88))
        self.assertEqual(len(build_context(response, Settings()).sources), 3)

    def test_empty_corrupt_chunk_is_an_availability_error(self):
        with self.assertLogs(level="ERROR"):
            with self.assertRaises(HTTPException) as caught:
                build_context(retrieved((chunk(text=""), 0.95)), Settings())
        self.assertEqual(caught.exception.status_code, 503)

    def test_context_metrics_expose_false_acceptance_and_valid_questions_rejected(self):
        service = Mock()
        service.retrieve.side_effect = [
            retrieved((chunk(), 0.9)), retrieved((chunk(), 0.8)), retrieved((chunk(), 0.9)),
        ]
        positive = {"id": "positive", "language": "en", "question": "Bag?", "source": "policy.docx",
                    "evidence": ["bag allowance"]}
        cases = [positive, {**positive, "id": "low"},
                 {**positive, "id": "negative", "source": None, "evidence": []}]
        report = evaluate(service, [chunk()], cases, 5, Settings())
        self.assertEqual(report["context_metrics"]["all"], {
            "questions": 3, "context_available": 2, "accepted_with_labelled_evidence": 1,
            "accepted_without_labelled_evidence": 1, "answerable_questions_rejected": 1,
            "unanswerable_questions_rejected": 0,
        })


if __name__ == "__main__":
    unittest.main()
