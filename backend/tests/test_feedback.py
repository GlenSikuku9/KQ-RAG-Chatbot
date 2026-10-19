from datetime import datetime, timezone
import unittest
from unittest.mock import ANY, Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.api.routes import feedback
from app.main import create_app
from app.models.conversation import Conversation, ConversationMessage, ConversationStatus, SenderType
from app.models.feedback import FeedbackRequest
from app.models.user import AuthenticatedUser
from app.services.feedback import FeedbackService
from app.services.operational_logging import OperationalLogger
from app.services.conversations import ConversationService
from test_conversations import MemoryClient, MemoryTransaction, run_locally


class FeedbackServiceTests(unittest.TestCase):
    def setUp(self):
        self.client = MemoryClient()
        self.service = FeedbackService(self.client)
        self.user, self.other = AuthenticatedUser(uid="passenger-1"), AuthenticatedUser(uid="passenger-2")
        self.now = datetime.now(timezone.utc)
        self.client.records = {
            "conversations": {"conversation-1": Conversation(
                conversation_id="conversation-1", user_id=self.user.uid, language="en",
                status=ConversationStatus.ACTIVE, started_at=self.now,
            ).model_dump()},
            "messages": {
                "bot-1": ConversationMessage(
                    message_id="bot-1", conversation_id="conversation-1", sequence=2, sender_type=SenderType.CHATBOT,
                    message_text="Answer", language="en", timestamp=self.now,
                ).model_dump(),
                "customer-1": ConversationMessage(
                    message_id="customer-1", conversation_id="conversation-1", sequence=1, sender_type=SenderType.CUSTOMER,
                    message_text="Question", language="en", timestamp=self.now,
                ).model_dump(),
            },
        }
        self.tx = patch("app.services.feedback.run_transaction", side_effect=run_locally).start()
        self.addCleanup(patch.stopall)

    def test_star_rating_creates_then_updates_one_deterministic_record(self):
        first = self.service.submit(self.user, "bot-1", FeedbackRequest(rating=5, comment="Useful"))
        second = self.service.submit(self.user, "bot-1", FeedbackRequest(rating=3))
        self.assertEqual(first.feedback_id, second.feedback_id)
        self.assertEqual(second.rating, 3)
        self.assertEqual(second.created_at, first.created_at)
        self.assertEqual(len(self.client.records["feedback"]), 1)
        self.assertNotIn("Useful", str(self.client.records.get("operational_events", {})))

    def test_only_owner_can_rate_chatbot_message_and_customer_messages_are_ineligible(self):
        with self.assertRaises(HTTPException) as caught:
            self.service.submit(self.other, "bot-1", FeedbackRequest(rating=4))
        self.assertEqual(caught.exception.status_code, 404)
        with self.assertRaises(HTTPException) as caught:
            self.service.submit(self.user, "customer-1", FeedbackRequest(rating=4))
        self.assertEqual(caught.exception.status_code, 422)
        self.assertFalse(self.client.records.get("feedback"))

    def test_rating_validation_rejects_non_star_values_before_storage(self):
        for rating in (0, 6, True, "5"):
            with self.subTest(rating=rating):
                with self.assertRaises(ValueError):
                    FeedbackRequest(rating=rating)
        self.assertFalse(self.client.records.get("feedback"))


class OperationalLoggingTests(unittest.TestCase):
    def test_event_has_timing_and_model_data_but_not_prompt_or_credentials(self):
        client = MemoryClient()
        logger = OperationalLogger(client)
        result = Mock(generation_called=True, decision="context_available")
        result.model = Mock(model_id="gemma", provider="openrouter", upstream_model="google/gemma")
        logger.completed(result, 12.5)
        stored = next(iter(client.records["operational_events"].values()))
        self.assertEqual(stored["duration_ms"], 12.5)
        self.assertEqual(stored["model_id"], "gemma")
        self.assertNotIn("prompt", stored)
        self.assertNotIn("api_key", stored)
        self.assertNotIn("user_id", stored)

    def test_chat_service_records_completion_fallback_and_failure_without_logging_content(self):
        store, answers, events = Mock(), Mock(), Mock()
        generated = Mock(generation_called=False, decision="fallback", model=None)
        answers.answer.return_value = generated
        service = ConversationService(store, answers, events)
        service.send(AuthenticatedUser(uid="passenger"), "conversation", Mock())
        events.completed.assert_called_once_with(generated, ANY)

        answers.answer.side_effect = RuntimeError("customer question and API key must not be logged")
        with self.assertRaises(RuntimeError):
            service.send(AuthenticatedUser(uid="passenger"), "conversation", Mock())
        events.failed.assert_called_once()


class FeedbackEndpointTests(unittest.TestCase):
    def setUp(self):
        self.user = AuthenticatedUser(uid="passenger-1")
        self.app = create_app()
        self.app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.app.dependency_overrides.clear)
        self.service = Mock()
        self.provider = patch.object(feedback, "get_feedback_service", return_value=self.service).start()
        self.addCleanup(patch.stopall)

    def test_endpoint_uses_verified_owner_and_rejects_invalid_stars(self):
        self.service.submit.return_value = {
            "feedback_id": "passenger-1:bot", "user_id": self.user.uid, "message_id": "bot", "rating": 5,
            "comment": None, "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc),
        }
        response = self.client.put("/api/v1/feedback/messages/bot?user_id=other", json={"rating": 5})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.service.submit.call_args.args[0], self.user)
        response = self.client.put("/api/v1/feedback/messages/bot", json={"rating": 6})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
