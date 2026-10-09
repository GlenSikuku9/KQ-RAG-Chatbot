import inspect
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.routes import retrieval
from app.config import Settings
from app.main import create_app
from app.models.user import AuthenticatedUser, UserRole
from app.rag.context import ContextService
from test_context import retrieved
from test_vector_index import chunk


class ContextEndpointTests(unittest.TestCase):
    def setUp(self):
        self.verify_patch = patch("app.api.dependencies.verify_firebase_id_token")
        self.verify = self.verify_patch.start()
        self.addCleanup(self.verify_patch.stop)
        self.verify.return_value = AuthenticatedUser(uid="test-admin", role=UserRole.ADMIN)
        self.retrieval = Mock()
        self.retrieval.retrieve.return_value = retrieved((chunk(), 0.9))
        self.provider_patch = patch.object(
            retrieval, "get_context_service", return_value=ContextService(self.retrieval, Settings()),
        )
        self.provider = self.provider_patch.start()
        self.addCleanup(self.provider_patch.stop)
        self.client = TestClient(create_app())
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer synthetic-context-test"}
        self.path = "/api/v1/retrieval/debug"

    def test_missing_auth_never_initializes_model_or_retrieval(self):
        result = self.client.post(self.path, json={"question": "Baggage?"})
        self.assertEqual(result.status_code, 401)
        self.verify.assert_not_called()
        self.provider.assert_not_called()

    def test_passenger_cannot_inject_admin_role(self):
        self.verify.return_value = AuthenticatedUser(uid="passenger", role=UserRole.PASSENGER)
        result = self.client.post(
            self.path + "?role=admin", headers={**self.headers, "X-Role": "admin"},
            json={"question": "Baggage?"},
        )
        self.assertEqual(result.status_code, 403)
        self.provider.assert_not_called()

    def test_invalid_or_expired_auth_is_not_a_successful_fallback(self):
        self.verify.side_effect = HTTPException(401, "Invalid or expired token.")
        result = self.client.post(self.path, headers=self.headers, json={"question": "Baggage?"})
        self.assertEqual(result.status_code, 401)
        self.provider.assert_not_called()

    def test_admin_receives_citations_raw_retrieval_and_explicit_heuristic_decision(self):
        result = self.client.post(
            self.path, headers=self.headers, json={"question": "Baggage?", "language": "en", "top_k": 3},
        )
        self.assertEqual(result.status_code, 200)
        data = result.json()
        self.assertEqual(data["decision"], "context_available")
        self.assertEqual(data["context"], "[S1]\nbag allowance")
        self.assertEqual(data["sources"][0]["chunk"]["chunk_id"], "one")
        self.assertEqual(data["sources"][0]["chunk"]["source_locations"], ["Body/block 1"])
        self.assertEqual(data["evidence_status"], "heuristic_only")
        self.assertFalse(data["generation_called"])
        self.assertNotIn("answer", data)
        self.assertEqual(self.retrieval.retrieve.call_args.args[0].top_k, 3)

    def test_invalid_body_never_initializes_model_or_retrieval(self):
        for payload in (
            {"question": " "}, {"question": "Baggage?", "top_k": True},
            {"question": "Baggage?", "language": "fr"},
            {"question": "Baggage?", "context_min_similarity": 0},
            {"question": "Baggage?", "role": "admin"},
        ):
            response = self.client.post(self.path, headers=self.headers, json=payload)
            self.assertEqual(response.status_code, 422)
        self.provider.assert_not_called()

    def test_low_evidence_is_explicit_clarification_with_no_context(self):
        self.retrieval.retrieve.return_value = retrieved((chunk(), 0.8))
        response = self.client.post(self.path, headers=self.headers, json={"question": "Baggage?"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["decision"], "clarification_required")
        self.assertEqual(response.json()["context"], "")
        self.assertTrue(response.json()["message"])

    def test_retrieval_errors_remain_errors_not_answerability_decisions(self):
        for status in (422, 503):
            self.retrieval.retrieve.side_effect = HTTPException(status, "Cannot retrieve.")
            response = self.client.post(self.path, headers=self.headers, json={"question": "Baggage?"})
            self.assertEqual(response.status_code, status)
            self.assertNotIn("decision", response.json())

    def test_blocking_inference_runs_in_worker_thread_route(self):
        self.assertFalse(inspect.iscoroutinefunction(retrieval.debug_retrieval))


if __name__ == "__main__":
    unittest.main()
