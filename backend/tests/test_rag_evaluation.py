"""Focused tests for transparent RAG metric calculations."""

from datetime import datetime, timezone
import unittest

from app.models.feedback import OperationalEvent
from app.services.rag_evaluation import aggregate_rag_operations


def event(
    event_id: str,
    event_type: str,
    duration_ms: float | None = None,
    **values,
) -> OperationalEvent:
    """Build a small operational fixture without treating it as project data."""

    return OperationalEvent(
        event_id=event_id,
        event_type=event_type,
        occurred_at=datetime.now(timezone.utc),
        duration_ms=duration_ms,
        **values,
    )


class RAGEvaluationTests(unittest.TestCase):
    def test_operational_metrics_use_rag_attempts_only(self):
        events = [
            event(
                "success",
                "chat_completed",
                1000,
                response_source="generated",
            ),
            event("failure", "chat_failed", 500),
            event(
                "flight",
                "flight_lookup_completed",
                25,
                response_source="flight_information",
                external_provider="OAG",
            ),
            event(
                "oag-request",
                "oag_api_request",
                20,
                external_provider="OAG",
            ),
            event(
                "greeting",
                "chat_completed",
                5,
                response_source="non_rag",
            ),
        ]

        metrics = aggregate_rag_operations(events)

        self.assertEqual(metrics["average_response_ms"].value, 1000)
        self.assertEqual(metrics["average_response_ms"].sample_size, 1)
        self.assertEqual(metrics["failure_rate"].value, 0.5)
        self.assertEqual(metrics["failure_rate"].sample_size, 2)

    def test_missing_operations_are_not_reported_as_zero(self):
        metrics = aggregate_rag_operations([])

        self.assertIsNone(metrics["average_response_ms"].value)
        self.assertEqual(
            metrics["average_response_ms"].status,
            "not_evaluated",
        )
        self.assertIsNone(metrics["failure_rate"].value)

    def test_retired_gemini_events_are_excluded(self):
        events = [
            event(
                "gemini-success",
                "chat_completed",
                250,
                model_id="google_gemini_flash",
                response_source="generated",
            ),
            event(
                "current-success",
                "chat_completed",
                1000,
                model_id="groq_gpt_oss_120b",
                response_source="generated",
            ),
        ]

        metrics = aggregate_rag_operations(events)

        self.assertEqual(metrics["average_response_ms"].value, 1000)
        self.assertEqual(metrics["average_response_ms"].sample_size, 1)
        self.assertEqual(metrics["failure_rate"].sample_size, 1)


if __name__ == "__main__":
    unittest.main()
