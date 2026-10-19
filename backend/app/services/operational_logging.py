"""Write minimal aggregate-ready operational events without customer content (I16)."""

import logging
from uuid import uuid4

from fastapi import HTTPException
from google.cloud.firestore_v1 import Client

from app.models.database import encode_document_id, utc_now
from app.models.feedback import OperationalEvent
from app.models.generation import GroundedAnswerResponse
from app.services.firestore_client import firestore_operation, get_firestore_client


logger = logging.getLogger(__name__)


class OperationalLogger:
    """A best-effort audit stream; events deliberately contain no passenger content."""

    def __init__(self, client: Client) -> None:
        self.client = client

    def completed(self, result: GroundedAnswerResponse, duration_ms: float) -> None:
        model = result.model
        event = OperationalEvent(
            event_id=uuid4().hex, event_type="chat_completed", occurred_at=utc_now(), duration_ms=duration_ms,
            model_id=model.model_id if model else None,
            model_provider=model.provider if model else None,
            model_version=model.upstream_model if model else None,
            response_source="generated" if result.generation_called else result.decision,
        )
        self._write(event)

    def failed(self, error: Exception, duration_ms: float) -> None:
        # Categorize errors without retaining the exception text, question, answer, UID, or token.
        event = OperationalEvent(
            event_id=uuid4().hex, event_type="chat_failed", occurred_at=utc_now(), duration_ms=duration_ms,
            failure_category=self._category(error),
        )
        self._write(event)

    def _write(self, event: OperationalEvent) -> None:
        with firestore_operation():
            self.client.collection("operational_events").document(encode_document_id(event.event_id)).set(event.model_dump())

    @staticmethod
    def _category(error: Exception) -> str:
        if isinstance(error, HTTPException):
            if error.status_code == 503:
                return "storage"
            if error.status_code in {422, 400}:
                return "validation"
        # Provider/retrieval exceptions are intentionally imported nowhere here so
        # logging remains decoupled from provider implementations and their details.
        name = type(error).__name__.lower()
        if "generation" in name or "provider" in name:
            return "provider"
        if "retriev" in name:
            return "retrieval"
        return "unknown"


def get_operational_logger() -> OperationalLogger:
    return OperationalLogger(get_firestore_client())
