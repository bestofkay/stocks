"""Auth request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.syncs import SyncAcceptedResponse


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    # Background provider refreshes kicked off for existing connections (non-blocking)
    syncs_started: list[SyncAcceptedResponse] = Field(default_factory=list)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    is_active: bool
    is_admin: bool
    created_at: datetime
