"""Passenger feedback routes; ratings apply only to the caller's chatbot messages."""

from fastapi import APIRouter, Depends

from app.api.dependencies import require_passenger_or_admin
from app.models.feedback import Feedback, FeedbackRequest
from app.models.user import AuthenticatedUser
from app.services.feedback import get_feedback_service


router = APIRouter()


@router.put("/messages/{message_id}", response_model=Feedback)
def submit_feedback(
    message_id: str,
    payload: FeedbackRequest,
    current_user: AuthenticatedUser = Depends(require_passenger_or_admin),
) -> Feedback:
    """Create or replace the caller's sole star rating for an eligible response."""
    return get_feedback_service().submit(current_user, message_id, payload)
