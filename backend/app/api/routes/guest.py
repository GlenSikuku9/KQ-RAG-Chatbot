"""Public, non-persistent chatbot access for passengers without an account."""

from fastapi import APIRouter, HTTPException

from app.models.generation import GroundedAnswerRequest, GroundedAnswerResponse
from app.rag.generation import GenerationProviderError
from app.rag.grounded_generation import get_grounded_answer_service
from app.services.oag_flights import OAGFlightError, get_oag_flight_service


router = APIRouter()


@router.post("/guest/messages", response_model=GroundedAnswerResponse)
def answer_guest_message(payload: GroundedAnswerRequest) -> GroundedAnswerResponse:
    """Answer one guest question without creating Firestore or log records.

    Conversation continuity stays in React for the current page session. The
    backend receives only the individual question needed for this RAG request.
    """
    try:
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
