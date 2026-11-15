"""Tests for official RAGAS input routing without external evaluator calls."""

from types import SimpleNamespace
import unittest

from app.services.ragas_evaluator import evaluate_report


class RecordingScorer:
    def __init__(self, value=0.8, error=None):
        self.value = value
        self.error = error
        self.calls = []

    async def ascore(self, **values):
        self.calls.append(values)
        if self.error:
            raise self.error
        return SimpleNamespace(value=self.value, reason="fixture")


class RagasEvaluatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scorers = {
            name: RecordingScorer()
            for name in (
                "context_precision",
                "context_recall",
                "faithfulness",
                "answer_relevance",
            )
        }
        self.metadata = {
            "ragas_version": "fixture",
            "evaluator_model": "fixture-judge",
            "embedding_method": "fixture-embedding",
            "evaluation_method": "ragas_collections_api_v0.4",
        }
        self.report = {
            "run_id": "source-run",
            "context_outcomes": [
                {
                    "case_id": "answerable",
                    "language": "en",
                    "question": "Question?",
                    "reference_answer": "Reference.",
                    "ragas_reference_status": "validated",
                    "context_metric_eligible": True,
                    "selected_contexts": ["First chunk", "Second chunk"],
                    "context_fingerprint": "context-1",
                },
                {
                    "case_id": "unsupported",
                    "language": "en",
                    "question": "Live account value?",
                    "reference_answer": "Fallback.",
                    "ragas_reference_status": "validated",
                    "context_metric_eligible": False,
                    "selected_contexts": [],
                    "context_fingerprint": "context-2",
                },
            ],
            "outcomes": [
                {
                    "case_id": "answerable",
                    "model_id": "model-a",
                    "answer": "Answer A.",
                    "generation_called": True,
                    "failure": None,
                },
                {
                    "case_id": "answerable",
                    "model_id": "model-b",
                    "answer": "Answer B.",
                    "generation_called": True,
                    "failure": None,
                },
                {
                    "case_id": "unsupported",
                    "model_id": "model-a",
                    "answer": "Fallback.",
                    "generation_called": False,
                    "failure": None,
                },
            ],
        }

    async def test_context_is_scored_once_and_responses_per_model(self):
        report = await evaluate_report(
            self.report,
            self.scorers,
            self.metadata,
        )

        self.assertEqual(len(self.scorers["context_precision"].calls), 1)
        self.assertEqual(len(self.scorers["context_recall"].calls), 1)
        self.assertEqual(len(self.scorers["faithfulness"].calls), 2)
        self.assertEqual(len(self.scorers["answer_relevance"].calls), 2)
        context_call = self.scorers["context_precision"].calls[0]
        self.assertEqual(
            context_call["retrieved_contexts"],
            ["First chunk", "Second chunk"],
        )
        self.assertEqual(context_call["reference"], "Reference.")
        self.assertFalse(
            any(item["case_id"] == "unsupported" for item in report["results"]),
        )

    async def test_failed_metric_does_not_create_a_score(self):
        self.scorers["faithfulness"] = RecordingScorer(
            error=TimeoutError("judge timeout"),
        )

        report = await evaluate_report(
            self.report,
            self.scorers,
            self.metadata,
        )
        failed = [
            item
            for item in report["results"]
            if item["metric_name"] == "faithfulness"
        ]

        self.assertTrue(failed)
        self.assertTrue(all(item["status"] == "failed" for item in failed))
        self.assertTrue(all("score" not in item for item in failed))
        self.assertFalse(
            any(
                item["metric_name"] == "faithfulness"
                for item in report["aggregates"]
            ),
        )
        timeout_summary = next(
            item
            for item in report["failure_summary"]
            if item["error_type"] == "TimeoutError"
        )
        self.assertEqual(timeout_summary["count"], 2)

    async def test_rate_limit_is_retried_before_succeeding(self):
        # Use a dynamic exception type because production detection intentionally
        # avoids importing a provider-specific exception into the evaluator.
        RateLimitError = type("RateLimitError", (Exception,), {})

        class RateLimitedOnce(RecordingScorer):
            async def ascore(self, **values):
                self.calls.append(values)
                if len(self.calls) == 1:
                    raise RateLimitError("try again in 0ms")
                return SimpleNamespace(value=0.7, reason="recovered")

        self.scorers["context_precision"] = RateLimitedOnce()
        report = await evaluate_report(
            self.report,
            self.scorers,
            self.metadata,
        )
        recovered = next(
            item
            for item in report["results"]
            if item["metric_name"] == "context_precision"
        )

        self.assertEqual(recovered["status"], "evaluated")
        self.assertEqual(recovered["attempts"], 2)

    async def test_resume_reuses_success_and_retries_only_missing_results(self):
        first = await evaluate_report(
            self.report,
            self.scorers,
            self.metadata,
        )
        prior = [
            item
            for item in first["results"]
            if item["metric_name"] == "context_precision"
        ]
        fresh_scorers = {
            name: RecordingScorer()
            for name in self.scorers
        }
        progress = []

        resumed = await evaluate_report(
            self.report,
            fresh_scorers,
            self.metadata,
            prior_results=prior,
            progress_callback=(
                lambda result, completed, total, results: progress.append(
                    (result["metric_name"], completed, total),
                )
            ),
        )

        self.assertEqual(len(fresh_scorers["context_precision"].calls), 0)
        self.assertEqual(len(fresh_scorers["context_recall"].calls), 1)
        self.assertEqual(len(resumed["results"]), 6)
        self.assertEqual(progress[-1][1:], (6, 6))


if __name__ == "__main__":
    unittest.main()
