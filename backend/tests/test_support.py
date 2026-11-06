import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.models.conversation import SendMessageRequest
from app.models.support import (
    SupportFeedbackCreate,
    SupportMessageCreate,
    SupportRequestCreate,
    SupportRequestType,
    SupportStatus,
)
from app.models.user import AuthenticatedUser, UserRole
from app.services.conversations import ConversationStore
from app.services.support import SupportService
from app.main import create_app
from test_conversations import (
    MemoryClient,
    generated_answer,
    run_locally,
)


class SupportLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.client = MemoryClient()
        self.service = SupportService(self.client)
        self.passenger = AuthenticatedUser(
            uid="passenger-1",
            email="passenger@example.com",
        )
        self.other = AuthenticatedUser(uid="passenger-2")
        self.agent = AuthenticatedUser(
            uid="admin-1",
            email="agent@example.com",
            display_name="KQ Agent",
            role=UserRole.ADMIN,
        )
        self.transaction = patch(
            "app.services.support.run_transaction",
            side_effect=run_locally,
        ).start()
        self.conversation_transaction = patch(
            "app.services.conversations.run_transaction",
            side_effect=run_locally,
        ).start()
        self.addCleanup(patch.stopall)

    def create_conversation(self):
        store = ConversationStore(self.client)
        return store.create_exchange(
            self.passenger,
            SendMessageRequest(question="My baggage issue", language="en"),
            generated_answer(),
        ).conversation

    def create_request(self, conversation_id):
        return self.service.create(
            self.passenger,
            SupportRequestCreate(
                conversation_id=conversation_id,
                request_type=SupportRequestType.LIVE,
                reason="explicit_request",
                issue_summary="I need help with my baggage.",
            ),
        )

    def test_create_claim_message_resolve_and_feedback_lifecycle(self):
        conversation = self.create_conversation()
        request = self.create_request(conversation.conversation_id)
        self.assertEqual(request.status, SupportStatus.PENDING)

        accepted = self.service.accept(self.agent, request.request_id)
        self.assertEqual(accepted.status, SupportStatus.IN_PROGRESS)
        stored_conversation = self.client.records["conversations"][
            conversation.conversation_id
        ]
        self.assertEqual(stored_conversation["support_mode"], "human")

        message = self.service.send_message(
            self.agent,
            request.request_id,
            SupportMessageCreate(
                message_text="I can assist with that.",
                client_message_id="agent-message-1",
            ),
            as_agent=True,
        )
        repeated = self.service.send_message(
            self.agent,
            request.request_id,
            SupportMessageCreate(
                message_text="I can assist with that.",
                client_message_id="agent-message-1",
            ),
            as_agent=True,
        )
        self.assertEqual(message.message_id, repeated.message_id)
        self.assertEqual(len(self.client.records["support_messages"]), 1)

        resolved = self.service.resolve(self.agent, request.request_id)
        self.assertEqual(resolved.status, SupportStatus.RESOLVED)
        repeated_resolution = self.service.resolve(
            self.agent,
            request.request_id,
        )
        self.assertEqual(
            repeated_resolution.status,
            SupportStatus.RESOLVED,
        )
        stored_conversation = self.client.records["conversations"][
            conversation.conversation_id
        ]
        self.assertEqual(stored_conversation["support_mode"], "ai")
        feedback = self.service.save_feedback(
            self.passenger,
            request.request_id,
            SupportFeedbackCreate(rating=5, comment="Helpful agent."),
        )
        self.assertEqual(feedback.rating, 5)

    def test_only_one_agent_can_claim_and_passenger_ownership_is_enforced(self):
        conversation = self.create_conversation()
        request = self.create_request(conversation.conversation_id)
        self.service.accept(self.agent, request.request_id)

        second_agent = self.agent.model_copy(
            update={"uid": "admin-2", "email": "second@example.com"},
        )
        with self.assertRaises(HTTPException) as caught:
            self.service.accept(second_agent, request.request_id)
        self.assertEqual(caught.exception.status_code, 409)

        with self.assertRaises(HTTPException) as caught:
            self.service.detail_for_passenger(self.other, request.request_id)
        self.assertEqual(caught.exception.status_code, 404)

    def test_active_handoff_blocks_rag_exchange(self):
        conversation = self.create_conversation()
        request = self.create_request(conversation.conversation_id)
        self.service.accept(self.agent, request.request_id)

        store = ConversationStore(self.client)
        with self.assertRaises(HTTPException) as caught:
            store.save_exchange(
                self.passenger,
                conversation.conversation_id,
                SendMessageRequest(question="Another question", language="en"),
                generated_answer(),
            )
        self.assertEqual(caught.exception.status_code, 409)

    def test_direct_handoff_creates_a_non_empty_conversation(self):
        request = self.create_request("direct-conversation")
        stored = self.client.records["conversations"]["direct-conversation"]
        messages = list(self.client.records["messages"].values())

        self.assertEqual(request.conversation_id, "direct-conversation")
        self.assertEqual(stored["next_sequence"], 2)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["sender_type"], "customer")

    def test_support_routes_are_published(self):
        paths = create_app().openapi()["paths"]
        self.assertIn("/api/v1/support/requests", paths)
        self.assertIn("/api/v1/support/availability", paths)
        self.assertIn("/api/v1/admin/support/requests", paths)
        self.assertIn("/api/v1/admin/support/summary", paths)


if __name__ == "__main__":
    unittest.main()
