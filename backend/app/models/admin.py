"""HTTP contracts for protected administrator workflows (I20/I21)."""

from typing import Literal

from pydantic import BaseModel, Field

from app.models.conversation import ConversationDetail
from app.models.database import UTCDateTime


class AdminDocument(BaseModel):
    document_id: str
    filename: str
    title: str
    category: str
    version: str | None = None
    uploaded_at: UTCDateTime
    uploaded_by: str | None = None
    status: str
    source: str
    chunk_count: int = 0


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    category: str | None = Field(default=None, min_length=1, max_length=120)


class AdminUser(BaseModel):
    user_id: str
    email: str | None = None
    name: str | None = None
    role: Literal["passenger", "admin"]
    disabled: bool
    email_verified: bool
    created_at: UTCDateTime | None = None
    last_login: UTCDateTime | None = None


class UserStatusUpdate(BaseModel):
    disabled: bool


class UserRoleUpdate(BaseModel):
    role: Literal["passenger", "admin"]


class AdminConversation(BaseModel):
    detail: ConversationDetail
    user_email: str | None = None


class AdminAuditEvent(BaseModel):
    activity_id: str
    admin_id: str
    action: str
    target_type: str
    target_id: str
    timestamp: UTCDateTime
    status: Literal["success", "failure"] = "success"
