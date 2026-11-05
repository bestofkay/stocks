"""Market / instrument listing API schemas."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class MarketResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_id: str
    name: str | None = None
    currency: str | None = None


class MarketListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: int
    provider_id: str
    markets: list[MarketResponse]


class InstrumentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    name: str | None = None
    market_id: str | None = None
    exchange_id: str | None = None
    currency: str | None = None
    last_price: Decimal | None = None
    asset_type: str | None = None
    provider_instrument_id: str | None = None


class InstrumentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: int
    provider_id: str
    market_id: str | None = None
    page: int = 1
    per_page: int = 100
    total: int | None = None
    instruments: list[InstrumentResponse] = Field(default_factory=list)
