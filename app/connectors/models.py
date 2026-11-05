"""Shared Pydantic models for normalized connector results."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ConnectorType(StrEnum):
    OFFICIAL_API = "official_api"
    AUTHENTICATED_HTTP = "authenticated_http"
    BROWSER_AUTOMATION = "browser_automation"
    STATEMENT_IMPORT = "statement_import"
    HYBRID = "hybrid"


class ImplementationStatus(StrEnum):
    """Distinguishes registry presence from verified operational support."""

    REGISTERED = "registered"  # Code present, not yet verified
    SCAFFOLDED = "scaffolded"  # Structure complete, selectors/endpoints unverified
    OPERATIONAL = "operational"  # Verified with integration tests against live provider


class AuthMethod(StrEnum):
    USERNAME_PASSWORD = "username_password"
    API_KEY = "api_key"
    OAUTH2 = "oauth2"
    SESSION_COOKIE = "session_cookie"
    STATEMENT_FILE = "statement_file"


class CredentialField(BaseModel):
    name: str
    label: str
    field_type: Literal["string", "password", "email", "file", "otp"] = "string"
    required: bool = True
    secret: bool = False
    description: str | None = None


class DataCapability(StrEnum):
    ACCOUNTS = "accounts"
    HOLDINGS = "holdings"
    CASH_BALANCE = "cash_balance"
    TRANSACTIONS = "transactions"
    PORTFOLIO_SUMMARY = "portfolio_summary"
    MFA = "mfa"
    DEPOSIT_INSTRUCTIONS = "deposit_instructions"
    ORDERS = "orders"
    INSTRUMENTS = "instruments"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class DepositInstructions(BaseModel):
    """Normalized funding / virtual-account instructions (read-only details)."""

    model_config = ConfigDict(extra="forbid")

    bank_name: str | None = None
    account_number: str | None = None
    account_name: str | None = None
    currency: str | None = None
    provider_reference: str | None = None
    bank_code: str | None = None
    raw: dict[str, Any] | None = None


class OrderRequest(BaseModel):
    """Normalized order placement request."""

    model_config = ConfigDict(extra="forbid")

    side: OrderSide
    symbol: str
    quantity: Decimal | None = None
    amount: Decimal | None = None
    account_external_id: str | None = None
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None
    tif: str = "DAY"
    expires: str | None = None
    pin: str | None = None


class OrderResult(BaseModel):
    """Normalized order placement result."""

    model_config = ConfigDict(extra="forbid")

    external_order_id: str | None = None
    status: str
    side: OrderSide | None = None
    symbol: str | None = None
    filled_qty: Decimal | None = None
    message: str | None = None
    raw: dict[str, Any] | None = None


class MarketInfo(BaseModel):
    """A tradeable market / exchange advertised by a connector."""

    model_config = ConfigDict(extra="forbid")

    market_id: str
    name: str | None = None
    currency: str | None = None


class MarketInstrument(BaseModel):
    """A tradeable instrument available for buy/sell orders."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    name: str | None = None
    market_id: str | None = None
    exchange_id: str | None = None
    currency: str | None = None
    last_price: Decimal | None = None
    asset_type: str | None = None
    provider_instrument_id: str | None = None


class InstrumentListResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_id: str | None = None
    page: int = 1
    per_page: int = 100
    total: int | None = None
    instruments: list[MarketInstrument] = Field(default_factory=list)


class ProviderMetadata(BaseModel):
    provider_id: str
    provider_name: str
    connector_type: ConnectorType
    implementation_status: ImplementationStatus
    auth_methods: list[AuthMethod]
    required_credential_fields: list[CredentialField]
    optional_credential_fields: list[CredentialField] = Field(default_factory=list)
    mfa_supported: bool = False
    capabilities: list[DataCapability] = Field(default_factory=list)
    approved_domains: list[str] = Field(default_factory=list)
    notes: str | None = None
    operational: bool = False  # True only when implementation_status == operational


class NormalizedAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_account_id: str
    account_name: str
    account_type: str | None = None
    currency: str | None = None
    cash_balance: Decimal | None = None


class NormalizedHolding(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    # Internal stable key; connectors may set explicitly
    security_identifier: str | None = None

    def resolve_security_identifier(self) -> str:
        if self.security_identifier:
            return self.security_identifier
        if self.isin:
            return f"isin:{self.isin}"
        if self.symbol and self.currency:
            return f"symbol:{self.symbol}:{self.currency}"
        if self.symbol:
            return f"symbol:{self.symbol}"
        return f"name:{self.security_name}"


class NormalizedTransaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_transaction_id: str
    transaction_type: str
    symbol: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    fees: Decimal | None = None
    total_amount: Decimal | None = None
    currency: str | None = None
    transaction_date: datetime | None = None


class PortfolioSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_market_value: Decimal | None = None
    total_cost_basis: Decimal | None = None
    cash_value: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    currency: str | None = None
    valuation_timestamp: datetime | None = None
    valuation_source: Literal["broker_supplied", "calculated"] = "broker_supplied"


class AuthResult(BaseModel):
    """Outcome of connector.authenticate()."""

    success: bool
    status: str  # authenticated | mfa_required | captcha_required | waiting_for_user | failed
    message: str | None = None
    mfa_challenge: dict[str, Any] | None = None
    session_metadata: dict[str, Any] | None = None  # non-secret status only
    # Merged into encrypted connection options after auth (e.g. Playwright storage_state).
    # Must never be returned from public API responses.
    options_patch: dict[str, Any] | None = None
