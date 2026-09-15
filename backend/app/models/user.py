from enum import Enum
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class UserRole(str, Enum):
    """Roles supported by the chatbot system."""

    PASSENGER = "passenger"
    ADMIN = "admin"


class AuthenticatedUser(BaseModel):
    """User identity extracted from a verified Firebase ID token."""

    uid: str = Field(..., description="Firebase Authentication user ID.")
    email: EmailStr | None = Field(default=None, description= "User email address.")
    display_name: str | None = Field(default=None, description= "Name from Firebase profile data.")
    role: UserRole = Field(default=UserRole.PASSENGER, description= "Role used for access control.")
    email_verified: bool = Field(default=False, description="Whether Firebase says the email has been verified.")
    disabled: bool = Field(default=False, description= "Reserved for Firebase account status checks.")

    @classmethod
    def from_firebase_claims(cls, claims: dict[str, Any]) -> "AuthenticatedUser":
        """Convert verified Firebase token claims into our application user model."""

        uid = claims.get("uid") or claims.get("user_id") or claims.get("sub")
        if not uid:
            raise ValueError("Verified Firebase token did not include a user ID.")

        raw_role = claims.get("role", UserRole.PASSENGER.value)
        role = UserRole(raw_role)

        return cls(
            uid=uid,
            email=claims.get("email"),
            display_name=claims.get("name"),
            role=role,
            email_verified=bool(claims.get("email_verified", False)),
        )
