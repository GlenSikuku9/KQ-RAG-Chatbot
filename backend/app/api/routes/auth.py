from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_user, require_admin
from app.models.user import AuthenticatedUser
from app.models.user_profile import ProfileSyncRequest, UserProfile
from app.services.user_profiles import get_user_profile, sync_user_profile


router = APIRouter()


@router.get("/profile", response_model=UserProfile)
def read_profile(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> UserProfile:
    """Read only the authenticated user's persisted profile."""

    return get_user_profile(current_user)


@router.post("/profile/sync", response_model=UserProfile)
def synchronize_profile(
    payload: ProfileSyncRequest | None = None,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> UserProfile:
    """Create or refresh the profile after login; no client-supplied fields are accepted."""

    return sync_user_profile(current_user)


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
