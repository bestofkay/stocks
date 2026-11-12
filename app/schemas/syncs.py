"""Sync run schemas."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class SyncRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    connection_id: int
    status: str
    current_step: str | None = None
    progress: Decimal
    started_at: datetime | None = None
    completed_at: datetime | None = None
    records_found: int
    accounts_found: int = 0
    holdings_found: int = 0
    error_code: str | None = None
    safe_error_message: str | None = None
    created_at: datetime


class SyncAcceptedResponse(BaseModel):
    sync_id: int
    status: str = "queued"
    message: str = "Synchronization accepted"
    poll_url: str
    connection_id: int | None = None


class SyncSummary(BaseModel):
    """Compact sync pointer attached to connection responses."""

    sync_id: int
    status: str
    poll_url: str
    current_step: str | None = None
    progress: Decimal | None = None
    safe_error_message: str | None = None


class SyncErrorResponse(BaseModel):
    id: int
    error_code: str
    safe_message: str
    created_at: datetime
