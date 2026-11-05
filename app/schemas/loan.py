"""Loan collateral and eligibility schemas (DB-backed purchased holdings only)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CollateralAsset(BaseModel):
    """One selectable purchased holding used as loan collateral."""

    model_config = ConfigDict(from_attributes=True)

    asset_id: str = Field(description="Stable id for selection: holding:{id}")
    asset_kind: str = Field(default="holding", description="Always holding (cash is excluded)")
    holding_id: int
    account_id: int
    connection_id: int
    provider_id: str
    provider_name: str | None = None
    account_name: str | None = None
    symbol: str | None = None
    security_name: str
    quantity: Decimal | None = None
    currency: str
    asset_value: Decimal = Field(description="Holding market value from last sync")
    valuation_timestamp: datetime | None = None
    observation_status: str | None = None
    last_synced_at: datetime | None = None


class ProviderCollateralTotal(BaseModel):
    connection_id: int
    provider_id: str
    provider_name: str | None = None
    display_name: str
    currency: str
    total_asset_value: Decimal
    asset_count: int
    eligible_loan: Decimal


class CurrencyCollateralTotal(BaseModel):
    currency: str
    total_asset_value: Decimal
    asset_count: int
    eligible_loan: Decimal


class CollateralResponse(BaseModel):
    loan_to_value_ratio: Decimal
    assets: list[CollateralAsset]
    by_provider: list[ProviderCollateralTotal]
    by_currency: list[CurrencyCollateralTotal]
    grand_total_asset_value: Decimal | None = Field(
        description="Sum of holding market values when a single currency is present; else null"
    )
    grand_total_eligible_loan: Decimal | None = Field(
        description="0.3 × grand_total_asset_value when single currency; else null"
    )
    as_of: datetime | None = None
    note: str = (
        "Collateral is purchased holdings only (market value from last sync). "
        "Cash / deposit balances are excluded. Totals are per currency (no FX). "
        "Eligible loan = loan_to_value_ratio × holding market value."
    )


class EligibilityResponse(BaseModel):
    loan_to_value_ratio: Decimal
    by_currency: list[CurrencyCollateralTotal]
    total_asset_value: Decimal | None = None
    eligible_loan: Decimal | None = None
    asset_count: int = 0
    as_of: datetime | None = None
    note: str = (
        "Eligible loan = loan_to_value_ratio × purchased holdings market value. "
        "Cash / deposits are not included. "
        "When multiple currencies exist, inspect by_currency; top-level totals are null."
    )


class SelectedEligibilityRequest(BaseModel):
    holding_ids: list[int] = Field(default_factory=list)
    asset_ids: list[str] = Field(
        default_factory=list,
        description="Optional alternate form: holding:{id}",
    )


class SelectedEligibilityResponse(EligibilityResponse):
    selected_assets: list[CollateralAsset]
    missing_asset_ids: list[str] = Field(default_factory=list)
