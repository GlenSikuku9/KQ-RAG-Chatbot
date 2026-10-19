"""Admin-only grounded-generation inspection before passenger chat arrives in I15."""

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import require_admin
from app.models.generation import GroundedAnswerRequest, GroundedAnswerResponse
from app.models.user import AuthenticatedUser
from app.rag.generation import GenerationProviderError
from app.rag.grounded_generation import get_grounded_answer_service

router = APIRouter()


@router.post("/answer", response_model=GroundedAnswerResponse)
def generate_grounded_answer(
    payload: GroundedAnswerRequest,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> GroundedAnswerResponse:
    """Inspect grounded generation; I15 will add the passenger conversation endpoint."""
    try:
        # Auth/input validation run before retrieval or an external provider request.
        return get_grounded_answer_service().answer(payload)
    except GenerationProviderError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
