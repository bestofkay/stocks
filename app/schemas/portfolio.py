"""Portfolio aggregation schemas."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CurrencyTotal(BaseModel):
    currency: str
    total_market_value: Decimal | None = None
    total_cost_basis: Decimal | None = None
    cash_value: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    holdings_count: int = 0


class PortfolioSummaryResponse(BaseModel):
    by_currency: list[CurrencyTotal]
    accounts_count: int
    holdings_count: int
    connections_count: int
    as_of: datetime | None = None
    note: str = "Totals are kept per currency; amounts are not FX-converted."


class HoldingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    connection_id: int | None = None
    provider_id: str | None = None
    account_name: str | None = None
    security_identifier: str
    symbol: str | None = None
    security_name: str
    isin: str | None = None
    asset_type: str | None = None
    quantity: Decimal | None = None
    average_cost: Decimal | None = None
    cost_basis: Decimal | None = None
    current_price: Decimal | None = None
    market_value: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    currency: str | None = None
    valuation_timestamp: datetime | None = None
    observation_status: str = "observed"


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    connection_id: int
    provider_id: str | None = None
    external_account_id: str
    account_name: str
    account_type: str | None = None
    currency: str | None = None
    cash_balance: Decimal | None = None
    last_seen_at: datetime | None = None


class BrokerPortfolioResponse(BaseModel):
    connection_id: int
    provider_id: str
    display_name: str
    by_currency: list[CurrencyTotal]
    holdings: list[HoldingResponse]


class SecurityAggregateResponse(BaseModel):
    security_key: str
    symbol: str | None = None
    security_name: str
    isin: str | None = None
    currency: str | None = None
    total_quantity: Decimal | None = None
    total_market_value: Decimal | None = None
    positions: list[HoldingResponse] = Field(default_factory=list)
    note: str = "Positions retained separately per broker; totals sum quantities only when currency matches."


class SnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    connection_id: int | None = None
    account_id: int | None = None
    total_market_value: Decimal | None = None
    total_cost_basis: Decimal | None = None
    cash_value: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    currency: str
    valuation_timestamp: datetime | None = None
    valuation_source: str
    captured_at: datetime


class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    external_transaction_id: str
    transaction_type: str
    symbol: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    fees: Decimal | None = None
    total_amount: Decimal | None = None
    currency: str | None = None
    transaction_date: datetime | None = None
