"""Pure parsers: InvestNaija (Zanibal) payloads -> normalized models."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

from app.connectors.models import (
    DepositInstructions,
    InstrumentListResult,
    MarketInstrument,
    NormalizedAccount,
    NormalizedHolding,
    NormalizedTransaction,
    OrderResult,
    PortfolioSummary,
)

WALLET_ACCOUNT_ID = "wallet:NGN"


def dec(v: Any) -> Decimal | None:
    if v is None or isinstance(v, bool
