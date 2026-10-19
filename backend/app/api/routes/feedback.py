"""Passenger feedback routes; ratings apply only after the caller ends a conversation."""

from fastapi import APIRouter, Depends

from app.api.dependencies import require_passenger_or_admin
from app.models.feedback import Feedback, FeedbackRequest
from app.models.user import AuthenticatedUser
from app.services.feedback import get_feedback_service


router = APIRouter()


@router.put("/conversations/{conversation_id}", response_model=Feedback)
def submit_feedback(
    conversation_id: str,
    payload: FeedbackRequest,
    current_user: AuthenticatedUser = Depends(require_passenger_or_admin),
) -> Feedback:
    """Create or replace the caller's sole rating after their conversation ends."""
    return get_feedback_service().submit(current_user, conversation_id, payload)
