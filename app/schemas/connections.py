"""Connection management schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConnectionCreate(BaseModel):
    provider_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    credentials: dict[str, Any]
    options: dict[str, Any] = Field(default_factory=dict)


class ConnectionUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    credentials: dict[str, Any] | None = None
    options: dict[str, Any] | None = None
    status: str | None = None


class ConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_id: str
    provider_key: str | None = None
    display_name: str
    status: str
    last_sync_at: datetime | None = None
    last_successful_sync_at: datetime | None = None
    last_error_code: str | None = None
    has_credentials: bool = True
    created_at: datetime
    updated_at: datetime


class ConnectionTestResponse(BaseModel):
    success: bool
    status: str
    message: str | None = None


class MfaSubmitRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
