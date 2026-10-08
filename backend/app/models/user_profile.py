from pydantic import AwareDatetime, BaseModel, ConfigDict, EmailStr, Field

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
    created_at: AwareDatetime
    last_login: AwareDatetime
