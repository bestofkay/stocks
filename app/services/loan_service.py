"""Loan collateral & eligibility from persisted purchased holdings.

Cash / deposit balances are excluded. Only holding market values from the
database (upserted by sync) count. Eligible loan = LTV × holdings value.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.db.models import (
    BrokerAccount,
    BrokerConnection,
    Holding,
    Provider,
    User,
)
from app.schemas.loan import (
    CollateralAsset,
    CollateralResponse,
    CurrencyCollateralTotal,
    EligibilityResponse,
    ProviderCollateralTotal,
    SelectedEligibilityRequest,
    SelectedEligibilityResponse,
)

_ZERO = Decimal("0")
_MONEY = Decimal("0.01")


def loan_ltv_ratio() -> Decimal:
    return Decimal(str(get_settings().loan_ltv_ratio))


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


def _eligible(value: Decimal, ratio: Decimal | None = None) -> Decimal:
    r = ratio if ratio is not None else loan_ltv_ratio()
    return _money(value * r)


def _holding_asset_id(holding_id: int) -> str:
    return f"holding:{holding_id}"


def _parse_holding_asset_ids(asset_ids: Iterable[str]) -> tuple[set[int], list[str]]:
    """Return (holding_ids, invalid_or_unsupported_ids). Cash ids are unsupported."""
    holdings: set[int] = set()
    invalid: list[str] = []
    for raw in asset_ids:
        aid = (raw or "").strip()
        if not aid:
            continue
        if ":" not in aid:
            invalid.append(aid)
            continue
        kind, _, rest = aid.partition(":")
        if kind == "cash":
            invalid.append(aid)
            continue
        try:
            num = int(rest)
        except ValueError:
            invalid.append(aid)
            continue
        if kind == "holding":
            holdings.add(num)
        else:
            invalid.append(aid)
    return holdings, invalid


def _load_collateral_rows(db: Session, user: User) -> list[CollateralAsset]:
    """Build selectable collateral from synced purchased holdings only."""
    assets: list[CollateralAsset] = []

    holding_rows = (
        db.query(Holding, BrokerAccount, BrokerConnection, Provider)
        .join(BrokerAccount, Holding.account_id == BrokerAccount.id)
        .join(BrokerConnection, BrokerAccount.connection_id == BrokerConnection.id)
        .join(Provider, BrokerConnection.provider_id == Provider.id)
        .filter(BrokerConnection.user_id == user.id)
        .filter(Holding.observation_status != "confirmed_absent")
        .all()
    )
    for h, acct, conn, prov in holding_rows:
        value = h.market_value if h.market_value is not None else _ZERO
        if value < 0:
            value = _ZERO
        ccy = (h.currency or acct.currency or "NGN").upper()
        assets.append(
            CollateralAsset(
                asset_id=_holding_asset_id(h.id),
                asset_kind="holding",
                holding_id=h.id,
                account_id=acct.id,
                connection_id=conn.id,
                provider_id=prov.provider_key,
                provider_name=prov.name,
                account_name=acct.account_name,
                symbol=h.symbol,
                security_name=h.security_name,
                quantity=h.quantity,
                currency=ccy,
                asset_value=_money(value),
                valuation_timestamp=h.valuation_timestamp,
                observation_status=h.observation_status,
                last_synced_at=conn.last_successful_sync_at,
            )
        )

    assets.sort(
        key=lambda a: (
            a.provider_id,
            a.connection_id,
            a.symbol or "",
            a.asset_id,
        )
    )
    return assets


def _currency_totals(
    assets: list[CollateralAsset],
    *,
    ratio: Decimal,
) -> list[CurrencyCollateralTotal]:
    buckets: dict[str, dict] = defaultdict(lambda: {"value": _ZERO, "count": 0})
    for a in assets:
        b = buckets[a.currency]
        b["count"] += 1
        b["value"] += a.asset_value
    return [
        CurrencyCollateralTotal(
            currency=ccy,
            total_asset_value=_money(vals["value"]),
            asset_count=vals["count"],
            eligible_loan=_eligible(vals["value"], ratio),
        )
        for ccy, vals in sorted(buckets.items())
    ]


def _provider_totals(
    assets: list[CollateralAsset],
    *,
    ratio: Decimal,
    connections: dict[int, BrokerConnection],
) -> list[ProviderCollateralTotal]:
    buckets: dict[tuple[int, str], dict] = defaultdict(
        lambda: {
            "value": _ZERO,
            "count": 0,
            "provider_id": "",
            "provider_name": None,
            "display_name": "",
        }
    )
    for a in assets:
        key = (a.connection_id, a.currency)
        b = buckets[key]
        b["count"] += 1
        b["provider_id"] = a.provider_id
        b["provider_name"] = a.provider_name
        conn = connections.get(a.connection_id)
        b["display_name"] = conn.display_name if conn else a.provider_id
        b["value"] += a.asset_value
    return [
        ProviderCollateralTotal(
            connection_id=connection_id,
            provider_id=vals["provider_id"],
            provider_name=vals["provider_name"],
            display_name=vals["display_name"],
            currency=currency,
            total_asset_value=_money(vals["value"]),
            asset_count=vals["count"],
            eligible_loan=_eligible(vals["value"], ratio),
        )
        for (connection_id, currency), vals in sorted(buckets.items())
    ]


def _single_currency_total(
    by_currency: list[CurrencyCollateralTotal],
) -> tuple[Decimal | None, Decimal | None]:
    if len(by_currency) != 1:
        return None, None
    only = by_currency[0]
    return only.total_asset_value, only.eligible_loan


def _as_of(assets: list[CollateralAsset]) -> datetime | None:
    times = [a.valuation_timestamp for a in assets if a.valuation_timestamp]
    times += [a.last_synced_at for a in assets if a.last_synced_at]
    return max(times) if times else None


def list_collateral(db: Session, user: User) -> CollateralResponse:
    ratio = loan_ltv_ratio()
    assets = _load_collateral_rows(db, user)
    connections = {
        c.id: c
        for c in db.query(BrokerConnection).filter(BrokerConnection.user_id == user.id).all()
    }
    by_currency = _currency_totals(assets, ratio=ratio)
    by_provider = _provider_totals(assets, ratio=ratio, connections=connections)
    grand_value, grand_loan = _single_currency_total(by_currency)
    return CollateralResponse(
        loan_to_value_ratio=ratio,
        assets=assets,
        by_provider=by_provider,
        by_currency=by_currency,
        grand_total_asset_value=grand_value,
        grand_total_eligible_loan=grand_loan,
        as_of=_as_of(assets),
    )


def eligibility_from_all_assets(db: Session, user: User) -> EligibilityResponse:
    """Eligible loan based on grand total of purchased holdings market values."""
    ratio = loan_ltv_ratio()
    assets = _load_collateral_rows(db, user)
    by_currency = _currency_totals(assets, ratio=ratio)
    total_value, eligible = _single_currency_total(by_currency)
    return EligibilityResponse(
        loan_to_value_ratio=ratio,
        by_currency=by_currency,
        total_asset_value=total_value,
        eligible_loan=eligible,
        asset_count=len(assets),
        as_of=_as_of(assets),
    )


def eligibility_from_selected(
    db: Session,
    user: User,
    body: SelectedEligibilityRequest,
) -> SelectedEligibilityResponse:
    """Eligible loan = LTV × sum(selected holding market values). Cash is not allowed."""
    ratio = loan_ltv_ratio()
    holding_ids = set(body.holding_ids or [])
    parsed_h, invalid = _parse_holding_asset_ids(body.asset_ids or [])
    holding_ids |= parsed_h

    if not holding_ids and not invalid:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Select at least one holding_id or asset_id (holding:{id}). Cash is not eligible.",
            status_code=422,
        )

    all_assets = _load_collateral_rows(db, user)
    by_id = {a.asset_id: a for a in all_assets}

    selected: list[CollateralAsset] = []
    missing: list[str] = list(invalid)

    for hid in sorted(holding_ids):
        aid = _holding_asset_id(hid)
        asset = by_id.get(aid)
        if asset is None:
            missing.append(aid)
        else:
            selected.append(asset)

    if missing and not selected:
        raise AppError(
            ErrorCode.ASSET_NOT_FOUND,
            "None of the requested holdings belong to this user "
            "(cash/deposit balances are not loan collateral)",
            status_code=404,
            details={"missing_asset_ids": missing},
        )

    by_currency = _currency_totals(selected, ratio=ratio)
    total_value, eligible = _single_currency_total(by_currency)
    return SelectedEligibilityResponse(
        loan_to_value_ratio=ratio,
        by_currency=by_currency,
        total_asset_value=total_value,
        eligible_loan=eligible,
        asset_count=len(selected),
        as_of=_as_of(selected),
        selected_assets=selected,
        missing_asset_ids=missing,
    )
