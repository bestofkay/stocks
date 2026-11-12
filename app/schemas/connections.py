"""Connection management schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.syncs import SyncAcceptedResponse, SyncSummary


class ConnectionCreate(BaseModel):
    provider_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    credentials: dict[str, Any]
    options: dict[str, Any] = Field(default_factory=dict)
    # Default true: one call stores credentials and starts provider sync
    sync: bool = True


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
    has_broker_session: bool = False
    latest_sync: SyncSummary | None = None
    # True when portfolio reads can use DB data from a successful sync
    portfolio_ready: bool = False
    created_at: datetime
    updated_at: datetime


class ConnectionCreateResponse(ConnectionResponse):
    """Create response includes the sync job started by default."""

    sync: SyncAcceptedResponse | None = None
    ready_url: str | None = None


class ConnectionReadyResponse(BaseModel):
    """Poll until ``ready`` is true, then call portfolio/deposit/order endpoints."""

    ready: bool
    connection_id: int
    status: str
    latest_sync: SyncSummary | None = None
    message: str
    ready_url: str | None = None


class ConnectionTestResponse(BaseModel):
    success: bool
    status: str
    message: str | None = None
    has_broker_session: bool = False


class MfaSubmitRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
