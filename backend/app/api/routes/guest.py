"""Public, non-persistent chatbot access for passengers without an account."""

from fastapi import APIRouter, HTTPException

from app.models.generation import GroundedAnswerRequest, GroundedAnswerResponse
from app.rag.generation import GenerationProviderError
from app.rag.grounded_generation import get_grounded_answer_service


router = APIRouter()


@router.post("/guest/messages", response_model=GroundedAnswerResponse)
def answer_guest_message(payload: GroundedAnswerRequest) -> GroundedAnswerResponse:
    """Answer one guest question without creating Firestore or log records.

    Conversation continuity stays in React for the current page session. The
    backend receives only the individual question needed for this RAG request.
    """
    try:
        return get_grounded_answer_service().answer(payload)
    except GenerationProviderError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.detail,
        ) from error
