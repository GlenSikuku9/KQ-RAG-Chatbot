"""Public read-only passenger theme endpoint."""

from fastapi import APIRouter

from app.models.admin import DEFAULT_PASSENGER_THEME, PassengerTheme
from app.services.firestore_client import firestore_operation, get_firestore_client

router = APIRouter()


@router.get("/theme", response_model=PassengerTheme)
def passenger_theme() -> PassengerTheme:
    """Return the latest published theme, falling back safely when unset."""
    try:
        with firestore_operation():
            snapshot = get_firestore_client().collection("app_settings").document("passenger_theme").get()
        if snapshot.exists:
            return PassengerTheme.model_validate(snapshot.to_dict())
    except Exception:
        # Theme availability must never prevent the passenger chatbot from loading.
        pass
    return PassengerTheme.model_validate(DEFAULT_PASSENGER_THEME)
