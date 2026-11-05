"""Deposit / funding-instruction API schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DepositInstructionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: int
    provider_id: str
    bank_name: str | None = None
    account_number: str | None = None
    account_name: str | None = None
    currency: str | None = None
    provider_reference: str | None = None
    bank_code: str | None = None
    raw: dict[str, Any] | None = Field(
        default=None,
        description="Provider-native fields (non-secret).",
    )
