"""Write minimal aggregate-ready operational events without customer content (I16)."""

import logging
import re
from uuid import uuid4

from fastapi import HTTPException
from google.cloud.firestore_v1 import Client

from app.models.database import encode_document_id, utc_now
from app.models.feedback import OperationalEvent
from app.models.generation import GroundedAnswerResponse
from app.models.generation import ConfiguredModel
from app.services.firestore_client import firestore_operation, get_firestore_client


logger = logging.getLogger(__name__)


class OperationalLogger:
    """A best-effort audit stream; events deliberately contain no passenger content."""

    def __init__(self, client: Client) -> None:
        self.client = client

    def completed(
        self,
        result: GroundedAnswerResponse,
        duration_ms: float,
        *,
        request_id: str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        """Record one successful generation request with trace identifiers."""
        model = result.model
        event = OperationalEvent(
            event_id=uuid4().hex,
            event_type="chat_completed",
            occurred_at=utc_now(),
            duration_ms=duration_ms,
            model_id=model.model_id if model else None,
            model_provider=model.provider if model else None,
            model_version=model.upstream_model if model else None,
            response_source=self._response_source(result),
            request_id=request_id,
            conversation_id=conversation_id,
        )
        self._write(event)

    def lifecycle(
        self,
        event_type: str,
        conversation_id: str,
        *,
        actor: str = "passenger",
    ) -> None:
        """Record a conversation operation without generation diagnostics."""
        event = OperationalEvent(
            event_id=uuid4().hex,
            event_type=event_type,
            occurred_at=utc_now(),
            conversation_id=conversation_id,
            actor=actor,
        )
        self._write(event)

    def flight_lookup(
        self,
        *,
        succeeded: bool,
        duration_ms: float,
        request_id: str,
        conversation_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        """Record provider health without storing a flight number or passenger text."""

        event = OperationalEvent(
            event_id=uuid4().hex,
            event_type=(
                "flight_lookup_completed"
                if succeeded
                else "flight_lookup_failed"
            ),
            occurred_at=utc_now(),
            duration_ms=duration_ms,
            response_source=(
                "flight_information"
                if succeeded
                else None
            ),
            request_id=request_id,
            conversation_id=conversation_id,
            error_code=error_code,
        )
        self._write(event)

    def oag_api_request(
        self,
        *,
        request_id: str,
        attempt_number: int,
        outcome: str,
        http_status: int | None = None,
    ) -> None:
        """Record exactly one outbound OAG HTTP attempt without query data."""

        event = OperationalEvent(
            event_id=uuid4().hex,
            event_type="oag_api_request",
            occurred_at=utc_now(),
            external_provider="OAG",
            http_status=http_status,
            request_outcome=outcome,
            request_id=request_id,
            attempt_number=attempt_number,
        )
        self._write(event)

    def failed(
        self,
        error: Exception,
        duration_ms: float,
        *,
        model: ConfiguredModel | None = None,
        failed_operation: str,
        request_id: str,
        failure_category: str | None = None,
    ) -> None:
        """Persist sanitized diagnostic context without passenger content."""
        category = failure_category or self.category(error)
        event = OperationalEvent(
            event_id=uuid4().hex,
            event_type="chat_failed",
            occurred_at=utc_now(),
            duration_ms=duration_ms,
            model_id=model.model_id if model else None,
            model_provider=model.provider if model else None,
            model_version=model.upstream_model if model else None,
            failure_category=category,
            failed_operation=failed_operation,
            error_code=self._error_code(error),
            error_summary=self._error_summary(error, category),
            request_id=request_id,
            model_selection_status=(
                "selected"
                if model
                else "not_selected"
            ),
        )
        self._write(event)

    def _write(self, event: OperationalEvent) -> None:
        with firestore_operation():
            self.client.collection("operational_events").document(encode_document_id(event.event_id)).set(event.model_dump())

    @staticmethod
    def _response_source(result: GroundedAnswerResponse) -> str:
        # Retrieval decisions use "clarification_required"; analytics uses the shorter
        # persisted message enum ("clarification") so events stay queryable.
        if result.generation_called:
            return "generated"
        if result.decision == "flight_information":
            return "flight_information"
        if result.decision == "clarification_required":
            return "clarification"
        return "fallback"

    @staticmethod
    def category(error: Exception) -> str:
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

    @staticmethod
    def _error_code(error: Exception) -> str:
        """Convert only the exception class into a bounded diagnostic code."""
        name = re.sub(
            r"(?<!^)(?=[A-Z])",
            "_",
            type(error).__name__,
        ).lower()
        return name[:100] or "unknown_error"

    @staticmethod
    def _error_summary(error: Exception, category: str) -> str:
        # HTTP and provider errors in this project expose intentionally safe
        # public detail. Unknown exceptions never contribute their raw text.
        detail = getattr(error, "detail", None)
        if isinstance(detail, str) and detail.strip():
            return detail.strip()[:500]

        summaries = {
            "provider": "The selected AI provider could not generate a response.",
            "storage": "The generated chat exchange could not be stored.",
            "retrieval": "Supporting knowledge could not be retrieved.",
            "validation": "The chat request did not pass validation.",
            "unknown": "The chat request failed during processing.",
        }
        return summaries.get(category, summaries["unknown"])


def get_operational_logger() -> OperationalLogger:
    return OperationalLogger(get_firestore_client())
