from fastapi import APIRouter, Depends

from app.api.dependencies import require_admin
from app.models.context import PreparedContext
from app.models.retrieval import RetrievalRequest
from app.models.user import AuthenticatedUser
from app.rag.context import get_context_service


router = APIRouter()


@router.post("/debug", response_model=PreparedContext)
def debug_retrieval(
    payload: RetrievalRequest,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> PreparedContext:
    """Inspect retrieval/context decisions without calling a generation model."""

    # Initialize model/index only after authorization and request validation succeed.
    return get_context_service().prepare(payload)
