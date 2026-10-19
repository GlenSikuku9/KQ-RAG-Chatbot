from datetime import datetime, timezone
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.models.context import ContextSource
from app.models.conversation import ConversationCreateRequest, ConversationStatus, SendMessageRequest
from app.models.document_chunk import DocumentChunk
from app.models.generation import GenerationResponse, GroundedAnswerResponse
from app.models.user import AuthenticatedUser
from app.api.dependencies import get_current_user
from app.main import create_app
from app.api.routes import conversations
from app.rag.generation import GenerationProviderError
from app.services.conversations import ConversationService, ConversationStore


class Snapshot:
    def __init__(self, payload):
        self.payload = payload
        self.exists = payload is not None
    def to_dict(self):
        return self.payload.copy() if self.payload else None


class MemoryReference:
    def __init__(self, client, collection, identifier):
        self.client, self.collection, self.identifier = client, collection, identifier
    def get(self, **kwargs):
        return Snapshot(self.client.records.setdefault(self.collection, {}).get(self.identifier))


class MemoryQuery:
    def __init__(self, client, collection):
        self.client, self.collection, self.field, self.value, self.order, self.descending = client, collection, None, None, None, False
    def where(self, *, filter):
        self.field, self.value = filter.field_path, filter.value
        return self
    def order_by(self, field, direction=None):
        self.order, self.descending = field, direction == "DESCENDING"
        return self
    def stream(self, **kwargs):
        values = list(self.client.records.setdefault(self.collection, {}).values())
        if self.field is not None:
            values = [value for value in values if value.get(self.field) == self.value]
        if self.order:
            values.sort(key=lambda value: value.get(self.order), reverse=self.descending)
        return [Snapshot(value) for value in values]


class MemoryCollection(MemoryQuery):
    def document(self, identifier):
        return MemoryReference(self.client, self.collection, identifier)


class MemoryClient:
    def __init__(self): self.records = {}
    def collection(self, name): return MemoryCollection(self, name)


class MemoryTransaction:
    def set(self, reference, payload):
        # Test storage applies the transaction atomically because the callback is local.
        reference.client.records.setdefault(reference.collection, {})[reference.identifier] = payload.copy()


def run_locally(client, operation):
    return operation(MemoryTransaction())


def source():
    chunk = DocumentChunk(
        chunk_id="chunk-1", document_id="document-1", version="v1", source="policy.docx",
        file_type="docx", category="baggage", chunk_index=0, text="23 kg allowance",
        source_locations=["Body/block 1"],
    )
    return ContextSource(citation_id="S1", retrieval_rank=1, score=0.91, chunk=chunk)


def generated_answer():
    return GroundedAnswerResponse(
        answer="The allowance is 23 kg.\n\nSources: [S1]", language="en", decision="context_available",
        generation_called=True,
        model=GenerationResponse(model_id="gemma", provider="openrouter", upstream_model="google/gemma-test", text="The allowance is 23 kg [S1]."),
        sources=[source()],
    )


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.client = MemoryClient()
        self.store = ConversationStore(self.client)
        self.user = AuthenticatedUser(uid="passenger-1")
        self.other = AuthenticatedUser(uid="passenger-2")
        self.transaction = patch("app.services.conversations.run_transaction", side_effect=run_locally).start()
        self.addCleanup(patch.stopall)

    def create(self):
        return self.store.create(self.user, ConversationCreateRequest(language="en"))

    def test_create_list_and_reopen_are_owned_and_ordered(self):
        first = self.create()
        second = self.create()
        listed = self.store.list_for_user(self.user)
        self.assertEqual({item.conversation_id for item in listed}, {first.conversation_id, second.conversation_id})
        detail = self.store.get_detail(self.user, first.conversation_id)
        self.assertEqual(detail.conversation.user_id, self.user.uid)
        self.assertEqual(detail.messages, [])
        with self.assertRaises(HTTPException) as caught:
            self.store.get_detail(self.other, first.conversation_id)
        self.assertEqual(caught.exception.status_code, 404)

    def test_exchange_persists_order_model_and_retrieval_traceability(self):
        conversation = self.create()
        request = SendMessageRequest(question="What baggage allowance applies?", language="en")
        result = self.store.save_exchange(self.user, conversation.conversation_id, request, generated_answer())
        self.assertEqual([result.customer_message.sequence, result.chatbot_message.sequence], [1, 2])
        self.assertEqual(result.chatbot_message.model_id, "gemma")
        self.assertEqual(result.chatbot_message.model_version, "google/gemma-test")
        self.assertEqual(result.chatbot_message.retrieved_chunks[0].chunk_id, "chunk-1")
        self.assertEqual(result.chatbot_message.retrieved_chunks[0].retrieval_rank, 1)
        self.assertAlmostEqual(result.chatbot_message.retrieved_chunks[0].similarity_score, 0.91)
        detail = self.store.get_detail(self.user, conversation.conversation_id)
        self.assertEqual([message.sequence for message in detail.messages], [1, 2])
        self.assertEqual(detail.messages[1].retrieved_chunks[0].citation_id, "S1")
        self.assertNotIn("retrieved_chunks", next(iter(self.client.records["messages"].values())))

    def test_ended_or_foreign_conversation_cannot_receive_messages(self):
        conversation = self.create()
        self.store.end(self.user, conversation.conversation_id)
        for user in (self.user, self.other):
            with self.subTest(user=user.uid), self.assertRaises(HTTPException) as caught:
                self.store.save_exchange(user, conversation.conversation_id, SendMessageRequest(question="Question?"), generated_answer())
            self.assertIn(caught.exception.status_code, {404, 409})
        self.assertFalse(self.client.records.get("messages"))

    def test_provider_failure_does_not_persist_a_successful_chatbot_answer(self):
        conversation = self.create()
        answers = Mock()
        answers.answer.side_effect = GenerationProviderError(503, "Provider unavailable")
        service = ConversationService(self.store, answers)
        with self.assertRaises(GenerationProviderError):
            service.send(self.user, conversation.conversation_id, SendMessageRequest(question="Question?"))
        self.assertFalse(self.client.records.get("messages"))
        self.assertFalse(self.client.records.get("message_chunks"))


class ConversationEndpointTests(unittest.TestCase):
    def setUp(self):
        self.user = AuthenticatedUser(uid="passenger-1")
        self.app = create_app()
        self.app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.app.dependency_overrides.clear)
        self.service = Mock()
        self.provider = patch.object(conversations, "get_conversation_service", return_value=self.service).start()
        self.addCleanup(patch.stopall)

    def test_routes_use_verified_user_not_client_supplied_owner(self):
        self.service.store.create.return_value = {
            "conversation_id": "one", "user_id": self.user.uid, "title": "New conversation", "language": "en",
            "status": "active", "started_at": datetime.now(timezone.utc), "ended_at": None,
        }
        response = self.client.post("/api/v1/conversations?user_id=other", json={"language": "en"})
        self.assertEqual(response.status_code, 201)
        self.service.store.create.assert_called_once()
        self.assertEqual(self.service.store.create.call_args.args[0], self.user)

    def test_invalid_send_body_and_anonymous_call_never_reach_service(self):
        response = self.client.post("/api/v1/conversations/one/messages", json={"question": " "})
        self.assertEqual(response.status_code, 422)
        self.service.send.assert_not_called()
        self.app.dependency_overrides.clear()
        response = self.client.post("/api/v1/conversations/one/messages", json={"question": "Baggage?"})
        self.assertEqual(response.status_code, 401)
        self.service.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
