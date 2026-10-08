from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer

from app.models.database import UTCDateTime
from app.models.user import UserRole


class ProfileSyncRequest(BaseModel):
    """Identity and role come exclusively from the verified token."""

    model_config = ConfigDict(extra="forbid")


class UserProfile(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    name: str | None = None
    email: EmailStr | None = None
    role: UserRole
    email_verified: bool
    created_at: UTCDateTime
    last_login: UTCDateTime

    @field_serializer("role")
    def serialize_role(self, role: UserRole) -> str:
        return role.value
