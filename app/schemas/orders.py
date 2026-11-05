"""Order placement API schemas."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OrderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: int
    side: Literal["buy", "sell"]
    symbol: str = Field(..., min_length=1, max_length=64)
    quantity: Decimal | None = None
    amount: Decimal | None = None
    account_external_id: str | None = Field(
        default=None,
        description="Broker portfolio / account id. Defaults to the first synced account.",
    )
    order_type: Literal["market", "limit"] = "market"
    limit_price: Decimal | None = None
    tif: str = "DAY"
    expires: str | None = None
    pin: str | None = Field(
        default=None,
        description="Optional 4-digit broker trading PIN when the provider requires it.",
    )

    @model_validator(mode="after")
    def _require_qty_or_amount(self) -> OrderCreateRequest:
        if self.quantity is None and self.amount is None:
            raise ValueError("Either quantity or amount is required")
        if self.order_type == "limit" and (self.limit_price is None or self.limit_price <= 0):
            raise ValueError("limit_price is required for limit orders")
        return self


class OrderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: int
    provider_id: str
    external_order_id: str | None = None
    status: str
    side: Literal["buy", "sell"] | None = None
    symbol: str | None = None
    filled_qty: Decimal | None = None
    message: str | None = None
    raw: dict[str, Any] | None = None
