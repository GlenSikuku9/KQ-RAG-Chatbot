from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.models.user import AuthenticatedUser, UserRole
from app.services.firebase_auth import verify_firebase_id_token


# Keep authentication errors consistent instead of using HTTPBearer's default responses.
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedUser:
    """Run blocking SDK verification in FastAPI's worker thread pool."""

    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required. Send a Firebase ID token using Bearer authentication.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication scheme. Use Bearer authentication.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return verify_firebase_id_token(credentials.credentials)


def require_roles(*allowed_roles: UserRole) -> Callable[..., AuthenticatedUser]:
    """Create a dependency that only allows users with specific roles."""

    async def role_checker(
        current_user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        # Role checks are centralized here so admin-only endpoints are protected consistently.
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this resource.",
            )

        return current_user

    return role_checker


# Common dependencies used by route modules.
require_passenger_or_admin = require_roles(UserRole.PASSENGER, UserRole.ADMIN)
require_admin = require_roles(UserRole.ADMIN)
