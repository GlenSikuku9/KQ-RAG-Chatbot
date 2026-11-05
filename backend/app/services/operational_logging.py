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

    def completed(self, result: GroundedAnswerResponse, duration_ms: float) -> None:
        model = result.model
        event = OperationalEvent(
            event_id=uuid4().hex, event_type="chat_completed", occurred_at=utc_now(), duration_ms=duration_ms,
            model_id=model.model_id if model else None,
            model_provider=model.provider if model else None,
            model_version=model.upstream_model if model else None,
            response_source=self._response_source(result),
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
