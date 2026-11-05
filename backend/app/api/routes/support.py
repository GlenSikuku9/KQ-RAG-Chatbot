"""Passenger-owned support handoff endpoints."""

from fastapi import APIRouter, Depends

from app.api.dependencies import require_passenger_or_admin
from app.models.support import (
    SupportAvailability,
    SupportFeedback,
    SupportFeedbackCreate,
    SupportMessage,
    SupportMessageCreate,
    SupportRequest,
    SupportRequestCreate,
    SupportRequestDetail,
)
from app.models.user import AuthenticatedUser
from app.services.support import get_support_service


router = APIRouter()


@router.get("/availability", response_model=SupportAvailability)
def availability() -> SupportAvailability:
    """Return only fresh agent presence, without exposing agent identities."""
    return get_support_service().availability()


@router.post("/requests", response_model=SupportRequest, status_code=201)
def create_request(
    payload: SupportRequestCreate,
    user: AuthenticatedUser = Depends(require_passenger_or_admin),
) -> SupportRequest:
    return get_support_service().create(user, payload)


@router.get("/requests", response_model=list[SupportRequest])
def list_requests(
    user: AuthenticatedUser = Depends(require_passenger_or_admin),
) -> list[SupportRequest]:
    return get_support_service().list_for_passenger(user)


@router.get(
    "/requests/{request_id}",
    response_model=SupportRequestDetail,
)
def request_detail(
    request_id: str,
    user: AuthenticatedUser = Depends(require_passenger_or_admin),
) -> SupportRequestDetail:
    return get_support_service().detail_for_passenger(user, request_id)


@router.post(
    "/requests/{request_id}/cancel",
    response_model=SupportRequest,
)
def cancel_request(
    request_id: str,
    user: AuthenticatedUser = Depends(require_passenger_or_admin),
) -> SupportRequest:
    return get_support_service().cancel(user, request_id)


@router.post(
    "/requests/{request_id}/convert-to-ticket",
    response_model=SupportRequest,
)
def convert_to_ticket(
    request_id: str,
    user: AuthenticatedUser = Depends(require_passenger_or_admin),
) -> SupportRequest:
    return get_support_service().convert_to_ticket(user, request_id)


@router.post(
    "/requests/{request_id}/messages",
    response_model=SupportMessage,
)
def send_message(
    request_id: str,
    payload: SupportMessageCreate,
    user: AuthenticatedUser = Depends(require_passenger_or_admin),
) -> SupportMessage:
    return get_support_service().send_message(
        user,
        request_id,
        payload,
        as_agent=False,
    )


@router.put(
    "/requests/{request_id}/feedback",
    response_model=SupportFeedback,
)
def save_feedback(
    request_id: str,
    payload: SupportFeedbackCreate,
    user: AuthenticatedUser = Depends(require_passenger_or_admin),
) -> SupportFeedback:
    return get_support_service().save_feedback(user, request_id, payload)
