from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_user, require_admin
from app.models.user import AuthenticatedUser


router = APIRouter()


@router.get("/me", response_model=AuthenticatedUser)
async def read_current_user(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    """Return the user represented by the Firebase ID token."""

    return current_user


@router.get("/admin-check", response_model=AuthenticatedUser)
async def check_admin_access(
    current_user: AuthenticatedUser = Depends(require_admin),
) -> AuthenticatedUser:
    """Small protected endpoint for confirming that admin role checks work."""

    return current_user
