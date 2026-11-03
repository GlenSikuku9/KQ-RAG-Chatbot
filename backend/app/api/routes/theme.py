"""Public read-only passenger theme endpoint."""

from fastapi import APIRouter

from app.models.admin import DEFAULT_PASSENGER_THEME, LEGACY_PASSENGER_THEME, PassengerTheme
from app.services.firestore_client import firestore_operation, get_firestore_client

router = APIRouter()


@router.get("/theme", response_model=PassengerTheme)
def passenger_theme() -> PassengerTheme:
    """Return the latest published theme, falling back safely when unset."""
    try:
        with firestore_operation():
            snapshot = get_firestore_client().collection("app_settings").document("passenger_theme").get()
        if snapshot.exists:
            stored_theme = snapshot.to_dict() or {}

            # Migrate only the former untouched defaults. Any palette an
            # administrator deliberately customised continues to take priority.
            if all(
                stored_theme.get(name) == value
                for name, value in LEGACY_PASSENGER_THEME.items()
            ):
                return PassengerTheme.model_validate(DEFAULT_PASSENGER_THEME)
            return PassengerTheme.model_validate(stored_theme)
    except Exception:
        # Theme availability must never prevent the passenger chatbot from loading.
        pass
    return PassengerTheme.model_validate(DEFAULT_PASSENGER_THEME)
