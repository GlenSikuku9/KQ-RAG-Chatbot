from enum import Enum
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class UserRole(str, Enum):
    """Roles supported by the chatbot system."""

    PASSENGER = "passenger"
    ADMIN = "admin"


class UnsupportedUserRoleError(ValueError):
    """A verified token contains an application role we do not support."""


class AuthenticatedUser(BaseModel):
    """User identity extracted from a verified Firebase ID token."""

    uid: str = Field(..., min_length=1, max_length=128, description="Firebase Authentication user ID.")
    email: EmailStr | None = Field(default=None, description="User email address.")
    display_name: str | None = Field(default=None, description="Name from Firebase profile data.")
    role: UserRole = Field(default=UserRole.PASSENGER, description="Role used for access control.")
    email_verified: bool = Field(default=False, description="Whether Firebase says the email has been verified.")
    disabled: bool = Field(default=False, description="False for users who pass the Firebase disabled-account check.")
    # Internal sign-in time: token refreshes must not count as new logins.
    auth_time: int | None = Field(default=None, strict=True, ge=0, le=253402300799, exclude=True)

    @classmethod
    def from_firebase_claims(cls, claims: dict[str, Any]) -> "AuthenticatedUser":
        """Convert verified Firebase token claims into our application user model."""

        uid = claims.get("uid") or claims.get("user_id") or claims.get("sub")
        if not isinstance(uid, str) or not uid.strip():
            raise ValueError("Verified Firebase token did not include a user ID.")

        # Only verified custom claims determine permissions, never sign-in provider or client input.
        raw_role = claims.get("role", UserRole.PASSENGER.value)
        if not isinstance(raw_role, str) or raw_role not in {role.value for role in UserRole}:
            raise UnsupportedUserRoleError("Verified token contains an unsupported role.")
        role = UserRole(raw_role)

        return cls(
            uid=uid,
            email=claims.get("email"),
            display_name=claims.get("name"),
            role=role,
            email_verified=bool(claims.get("email_verified", False)),
            auth_time=claims.get("auth_time"),
        )
