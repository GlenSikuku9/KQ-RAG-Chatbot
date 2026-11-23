"""Mocked tests for criterion-aware experimental evaluation."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from scripts.experimental_evaluation import (
    aggregate_generation,
    aggregate_offline,
    build_request,
    evidence_metrics,
    run_generation_calls,
)


def fixture_case() -> dict:
    """Return two requirements with alternatives for deterministic scoring."""

    return {
        "case_id": "case-en",
        "semantic_group": "group",
        "partition": "development",
        "language": "en",
        "question": "What applies?",
        "fixed_history": None,
        "expected_behavior": "answer",
        "reference_answer": "Reference",
        "metric_eligibility": {
            "retrieval_and_context_recall": "eligible",
            "answer_correctness": "eligible",
        },
        "mandatory_evidence_criteria": [
            {
                "criterion_id": "requirement-1",
                "policy_text_locator": "first",
                "acceptable_parent_chunk_ids": ["a", "a-alternative"],
            },
            {
                "criterion_id": "requirement-2",
                "policy_text_locator": "second",
                "acceptable_parent_chunk_ids": ["b"],
            },
        ],
    }


class CriterionMetricTests(unittest.TestCase):
    def test_equivalent_parent_satisfies_one_requirement_without_penalty(self):
        scores, matches = evidence_metrics(
            fixture_case(),
            ["a-alternative", "irrelevant", "b"],
            5,
            precision_denominator=5,
            scope="retrieval_at_k",
        )

        self.assertEqual(scores["precision"]["value"], 0.4)
        self.assertEqual(scores["recall"]["value"], 1.0)
        self.assertEqual(scores["mrr"]["value"], 1.0)
        self.assertTrue(all(item["satisfied"] for item in matches))

    def test_missing_second_requirement_reduces_criterion_recall(self):
        scores, _ = evidence_metrics(
            fixture_case(),
            ["a"],
            5,
            precision_denominator=5,
            scope="retrieval_at_k",
        )

        self.assertEqual(scores["recall"]["numerator"], 1)
        self.assertEqual(scores["recall"]["denominator"], 2)
        self.assertEqual(scores["recall"]["value"], 0.5)

    def test_behavior_only_case_records_non_applicability(self):
        case = fixture_case()
        case["metric_eligibility"]["retrieval_and_context_recall"] = (
            "excluded_behavior_only"
        )
        case["mandatory_evidence_criteria"] = []

        scores, matches = evidence_metrics(
            case,
            ["irrelevant"],
            5,
            precision_denominator=5,
            scope="retrieval_at_k",
        )

        self.assertFalse(scores["recall"]["applicable"])
        self.assertIsNone(scores["recall"]["value"])
        self.assertEqual(matches, [])

    def test_fixed_history_uses_production_conversation_resolver(self):
        case = fixture_case()
        case["question"] = "Does that also apply when travelling to London?"
        case["fixed_history"] = (
            "USER: When does international online check-in open?\n"
            "ASSISTANT: Usually 48 hours before departure."
        )

        request = build_request(case, 5)

        self.assertEqual(len(request.conversation_context), 2)
        self.assertIn("online check-in", request.resolved_question)
        self.assertIn("London", request.resolved_question)

    def test_aggregate_exposes_applicable_and_missing_denominators(self):
        score = {
            "value": 0.5,
            "applicable": True,
            "numerator": 1,
            "denominator": 2,
            "method": "fixture",
            "missing_reason": None,
        }
        case = {
            "language": "en",
            "retrieval": {
                "retrieval_ms": 2.0,
                "metrics": {
                    "precision_at_k": score,
                    "criterion_recall_at_k": score,
                    "mrr_at_k": score,
                },
            },
            "context": {
                "context_build_ms": 1.0,
                "metrics": {
                    "context_precision": score,
                    "context_recall": score,
                },
            },
        }

        aggregates = aggregate_offline([case])

        self.assertEqual(
            aggregates["all"]["criterion_recall_at_5"]["scored_cases"],
            1,
        )
        self.assertEqual(
            aggregates["sw"]["criterion_recall_at_5"]["total_cases"],
            0,
        )


class RecordingGenerationService:
    def __init__(self):
        self.calls = []

    def configured_models(self):
        models = {
            "openrouter_gemma_26b": "google/gemma-4-26b-a4b-it",
            "openrouter_gemma_31b": "google/gemma-4-31b-it",
            "groq_gpt_oss_120b": "openai/gpt-oss-120b",
        }
        return [
            SimpleNamespace(
                model_id=model_id,
                upstream_model=upstream,
                configured=True,
                model_dump=(
                    lambda mode, model_id=model_id, upstream=upstream: {
                        "model_id": model_id,
                        "upstream_model": upstream,
                        "configured": True,
                    }
                ),
            )
            for model_id, upstream in models.items()
        ]

    def generate(self, prompt, model_id, system_instruction):
        self.calls.append((prompt, model_id, system_instruction))
        return SimpleNamespace(
            text=f"answer from {model_id}",
            provider_request_id="fixture-request",
            provider_duration_ms=1.0,
            attempt_count=1,
        )


class ResumeTests(unittest.TestCase):
    def test_completed_generation_calls_are_not_repeated(self):
        report = {
            "run_status": "offline_development_complete",
            "cases": [
                {
                    "case_id": "case-en",
                    "language": "en",
                    "generation_input": {"prompt": "fixture prompt"},
                    "context": {"message": None},
                },
            ],
        }
        service = RecordingGenerationService()
        with TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "checkpoint.json"
            first = run_generation_calls(
                report,
                service,
                checkpoint,
                authorised=True,
            )
            call_count = len(service.calls)
            second = run_generation_calls(
                report,
                service,
                checkpoint,
                authorised=True,
            )

            self.assertEqual(call_count, 3)
            self.assertEqual(len(service.calls), call_count)
            self.assertEqual(len(first), 3)
            self.assertEqual(len(second), 3)
            self.assertEqual(
                len(json.loads(checkpoint.read_text())["outcomes"]),
                3,
            )

    def test_generation_aggregation_separates_failure_from_fallback(self):
        case = fixture_case()
        case["context"] = {"decision": "context_available"}
        report = {"cases": [case]}
        outcomes = [
            {
                "case_id": "case-en",
                "model_id": "openrouter_gemma_26b",
                "language": "en",
                "status": "failed",
                "generation_called": True,
                "response_time_ms": 10.0,
            },
        ]

        result = aggregate_generation(report, outcomes)
        aggregate = result["aggregates"]["openrouter_gemma_26b"]["all"]

        self.assertEqual(aggregate["failure_rate"]["value"], 1.0)
        self.assertEqual(aggregate["fallback_accuracy"]["applicable_cases"], 0)

if __name__ == "__main__":
    unittest.main()
