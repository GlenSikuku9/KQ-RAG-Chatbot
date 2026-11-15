"""Public, non-persistent chatbot access for passengers without an account."""

from fastapi import APIRouter, HTTPException

from app.models.generation import GroundedAnswerRequest, GroundedAnswerResponse
from app.config import get_settings
from app.rag.generation import GenerationProviderError
from app.rag.grounded_generation import (
    answer_static_intent,
    get_grounded_answer_service,
)
from app.services.oag_flights import OAGFlightError, get_oag_flight_service
from app.services.language_detection import get_language_detector
from app.services.conversation_context import resolve_conversation_query
from app.services.chat_intents import (
    classify_static_intent,
    static_intent_language,
)


router = APIRouter()


@router.post("/guest/messages", response_model=GroundedAnswerResponse)
def answer_guest_message(payload: GroundedAnswerRequest) -> GroundedAnswerResponse:
    """Answer one guest question without creating Firestore or log records.

    Conversation continuity stays in React for the current page session. The
    backend receives only the individual question needed for this RAG request.
    """
    try:
        static_intent = classify_static_intent(payload.question)
        if static_intent and static_intent.intent in {
            "greeting",
            "acknowledgement",
        }:
            payload = payload.model_copy(
                update={
                    "language": static_intent_language(
                        payload.question,
                        payload.previous_language,
                    ),
                    "conversation_context": [],
                    "resolved_question": payload.question,
                },
            )
            return answer_static_intent(payload)

        detected = get_language_detector().detect(
            payload.question,
            payload.previous_language,
        )
        payload = payload.model_copy(
            update={"language": detected.language},
        )
        context_ids = {
            item.conversation_id
            for item in payload.conversation_context
        }
        guest_context = (
            payload.conversation_context
            if len(context_ids) <= 1
            else []
        )
        resolution = resolve_conversation_query(
            payload.question,
            guest_context,
            history_messages=(
                get_settings().conversation_history_exchanges * 2
            ),
        )
        payload = payload.model_copy(
            update={
                "conversation_context": list(resolution.context),
                "resolved_question": resolution.resolved_question,
            },
        )
        flight_answer = get_oag_flight_service().answer_if_flight_query(payload)
        return flight_answer or get_grounded_answer_service().answer(payload)
    except OAGFlightError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.detail,
        ) from error
    except GenerationProviderError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.detail,
        ) from error
