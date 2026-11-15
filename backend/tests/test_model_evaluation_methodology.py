"""Regression tests for unique-question document-overlap evaluation."""

from datetime import datetime, timezone
import unittest

from scripts.evaluate_models import _context_records, _source_overlap


class ModelEvaluationMethodologyTests(unittest.TestCase):
    def test_source_overlap_is_document_level(self):
        precision, recall = _source_overlap(
            {"Primary.docx", "Extra.docx"},
            {"Primary.docx"},
        )

        self.assertEqual(precision, 0.5)
        self.assertEqual(recall, 1.0)

    def test_context_records_exclude_unsupported_cases(self):
        outcomes = [
            {
                "language": "en",
                "context_metric_eligible": True,
                "document_source_precision": 0.5,
                "document_source_recall": 1.0,
                "decision": "context_available",
                "expected_fallback": False,
            },
            {
                "language": "en",
                "context_metric_eligible": False,
                "document_source_precision": None,
                "document_source_recall": None,
                "decision": "fallback",
                "expected_fallback": True,
            },
        ]

        records = _context_records(
            outcomes,
            "run-1",
            datetime.now(timezone.utc),
            {
                "dataset_id": "fixture",
                "version": "1",
            },
            "kb-1",
        )
        overall = next(
            record
            for record in records
            if record["language"] == "all"
        )

        self.assertEqual(overall["context_sample_size"], 1)
        self.assertEqual(overall["fallback_sample_size"], 2)
        self.assertEqual(overall["context_precision"], 0.5)
        self.assertEqual(overall["context_recall"], 1.0)
        self.assertEqual(overall["fallback_accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
