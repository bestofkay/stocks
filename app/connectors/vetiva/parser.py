"""Pure parsers: Vetiva JSON payloads -> normalized models.

Responses are wrapped as ``{message, status_code, data}``; errors as
``{error, reason, status_code}``. Unknown numbers stay ``None``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

from app.connectors.models import (
    InstrumentListResult,
    MarketInstrument,
    NormalizedAccount,
    NormalizedHolding,
    NormalizedTransaction,
    OrderResult,
    PortfolioSummary,
)

PORTFOLIO_ACCOUNT_PREFIX = "portfolio:"
WALLET_ACCOUNT_PREFIX = "wallet:"

_ASSET_TYPES = {
    "STOCKS": "equity",
    "STOCK": "equity",
    "EQUITY": "equity",
    "FIXED_INCOME": "fixed_income",
    "MONEY_MARKET": "money_market",
    "TRUSTS": "trust",
}


def to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip().replace(",", "").replace("₦", "")
    if not text or text in {"-", "—", "N/A", "null"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str) or value.startswith("0001-"):
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def broker_message(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("error", "message"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


# --- auth ---------------------------------------------------------------


def session_from_auth(payload: Any) -> dict[str, Any]:
    """Login / verify-2fa / refresh all return ``data.session``."""
    data = unwrap(payload)
    data = data if isinstance(data, dict) else {}
    sess = data.get("session") if isinstance(data.get("session"), dict) else {}
    account = data.get("account") if isinstance(data.get("account"), dict) else {}
    return {
        "access_token": sess.get("access_token"),
        "refresh_token": sess.get("refresh_token"),
        "expired_at": sess.get("expired_at"),
        "refresh_expires_at": sess.get("refresh_expires_at"),
        "account_id": sess.get("account_id") or account.get("account_id"),
        "is_2fa_enabled": bool(data.get("is2faenabled")),
    }


def profile_info(payload: Any) -> dict[str, Any]:
    data = unwrap(payload)
    data = data if isinstance(data, dict) else {}
    return {
        "account_id": data.get("account_id"),
        "has_trade_account": bool(data.get("has_trade_account")),
        "trade_account_status": data.get("trade_account_status") or None,
        "kyc_status": data.get("status"),
    }


# --- accounts / holdings ------------------------------------------------


def portfolio_account(account_id: str, *, trading_balance: Decimal | None) -> NormalizedAccount:
    return NormalizedAccount(
        external_account_id=f"{PORTFOLIO_ACCOUNT_PREFIX}{account_id}",
        account_name="Vetiva Portfolio",
        account_type="PORTFOLIO",
        currency="NGN",
        cash_balance=trading_balance,
    )


def wallet_accounts(payload: Any) -> list[NormalizedAccount]:
    rows = unwrap(payload)
    if not isinstance(rows, list):
        return []
    out: list[NormalizedAccount] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("wallet_id"):
            continue
        if (row.get("status") or "active").lower() not in {"active"}:
            continue
        ccy = (row.get("currency") or "NGN").upper()
        out.append(
            NormalizedAccount(
                external_account_id=f"{WALLET_ACCOUNT_PREFIX}{row['wallet_id']}",
                account_name=f"{ccy} Wallet",
                account_type="WALLET",
                currency=ccy,
                cash_balance=to_decimal(row.get("available_balance") if row.get("available_balance") is not None else row.get("balance")),
            )
        )
    return out


def wallet_balance(payload: Any, wallet_id: str) -> Decimal | None:
    for acct in wallet_accounts(payload):
        if acct.external_account_id == f"{WALLET_ACCOUNT_PREFIX}{wallet_id}":
            return acct.cash_balance
    return None


def portfolio_value(payload: Any) -> dict[str, Decimal | None]:
    data = unwrap(payload)
    data = data if isinstance(data, dict) else {}
    return {k: to_decimal(data.get(k)) for k in ("portfolio_value", "wallet_balance", "trading_balance", "total_balance")}


def assets_page(payload: Any) -> tuple[list[dict[str, Any]], int]:
    data = unwrap(payload)
    if not isinstance(data, dict):
        return [], 0
    rows = [r for r in data.get("assets") or [] if isinstance(r, dict)]
    try:
        total = int(data.get("total_count") or 0)
    except (TypeError, ValueError):
        total = len(rows)
    return rows, total


def holdings_from_assets(rows: list[dict[str, Any]], *, currency: str = "NGN") -> list[NormalizedHolding]:
    holdings: list[NormalizedHolding] = []
    for row in rows:
        name = row.get("asset_name") or row.get("asset_symbol")
        if not name:
            continue
        raw_type = (row.get("asset_type") or "").upper()
        qty = to_decimal(row.get("quantity_or_units"))
        value = to_decimal(row.get("amount"))
        pnl = to_decimal(row.get("unrealized_pnl"))
        cost = value - pnl if value is not None and pnl is not None else None
        price = (value / qty) if value is not None and qty else None
        avg = (cost / qty) if cost is not None and qty else None
        symbol = (row.get("asset_symbol") or "").strip() or None
        holdings.append(
            NormalizedHolding(
                symbol=symbol,
                security_name=str(name),
                asset_type=_ASSET_TYPES.get(raw_type, raw_type.lower() or None),
                quantity=qty,
                average_cost=avg,
                cost_basis=cost,
                current_price=price,
                market_value=value,
                unrealized_pnl=pnl,
                currency=row.get("currency") or currency,
                security_identifier=None if symbol else (f"vetiva:{row['id']}" if row.get("id") else None),
            )
        )
    return holdings


def summary_from_breakdown(value_payload: Any, breakdown_payload: Any) -> PortfolioSummary:
    pv = portfolio_value(value_payload)
    bd = unwrap(breakdown_payload)
    bd = bd if isinstance(bd, dict) else {}
    pnl = to_decimal(bd.get("unrealized_pnl"))
    mv = pv["portfolio_value"] if pv["portfolio_value"] is not None else to_decimal(bd.get("portfolio_value"))
    return PortfolioSummary(
        total_market_value=mv,
        total_cost_basis=(mv - pnl) if mv is not None and pnl is not None else None,
        cash_value=pv["trading_balance"],
        unrealized_pnl=pnl,
        currency="NGN",
        valuation_source="broker_supplied",
    )


def _signed_amount(row: dict[str, Any]) -> Decimal | None:
    amt = to_decimal(row.get("amount"))
    if amt is None:
        return None
    kind = (row.get("type") or row.get("entry_type") or "").lower()
    return -abs(amt) if kind in {"debit", "dr"} else amt


def transactions_from_records(payload: Any, *, prefix: str) -> list[NormalizedTransaction]:
    data = unwrap(payload)
    rows = data.get("records") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    out: list[NormalizedTransaction] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        ext = row.get("id") or row.get("transaction_reference") or row.get("reference") or f"{row.get('created_at')}:{idx}"
        out.append(
            NormalizedTransaction(
                external_transaction_id=f"{prefix}:{ext}",
                transaction_type=str(row.get("purpose") or row.get("category") or row.get("type") or "unknown").lower(),
                symbol=row.get("symbol"),
                quantity=to_decimal(row.get("qty") or row.get("quantity")),
                unit_price=to_decimal(row.get("price") or row.get("px")),
                fees=to_decimal(row.get("fee") or row.get("fees")),
                total_amount=_signed_amount(row),
                currency=row.get("currency") or "NGN",
                transaction_date=parse_dt(row.get("created_at") or row.get("transaction_date") or row.get("date")),
            )
        )
    return out


# --- instruments / orders -----------------------------------------------


def securities_page(payload: Any) -> tuple[list[dict[str, Any]], bool]:
    data = unwrap(payload)
    if not isinstance(data, dict):
        return [], False
    rows = [r for r in data.get("items") or [] if isinstance(r, dict)]
    return rows, bool(data.get("has_more"))


def instruments_result(rows: list[dict[str, Any]], *, search: str | None, page: int, per_page: int) -> InstrumentListResult:
    term = (search or "").strip().lower()
    items: list[MarketInstrument] = []
    for row in rows:
        sym = (row.get("symbol") or "").strip()
        if not sym or row.get("isActive") is False:
            continue
        if term and term not in sym.lower() and term not in (row.get("name") or "").lower():
            continue
        items.append(
            MarketInstrument(
                symbol=sym,
                name=row.get("name"),
                market_id="NGX",
                exchange_id=row.get("mktId"),
                currency="NGN",
                last_price=to_decimal(row.get("refPx")),
                asset_type="equity" if (row.get("mktSeg") or "").upper() in {"EQTY", "PREMIUM", "GROWTH", "ASEM"} else (row.get("type") or "").lower() or None,
                provider_instrument_id=sym,
            )
        )
    items.sort(key=lambda i: i.symbol)
    page = max(1, int(page))
    per_page = max(1, min(int(per_page), 500))
    start = (page - 1) * per_page
    return InstrumentListResult(market_id="NGX", page=page, per_page=per_page, total=len(items), instruments=items[start : start + per_page])


def last_price_from_stats(payload: Any) -> Decimal | None:
    data = unwrap(payload)
    if isinstance(data, dict):
        for key in ("lastPx", "refPx", "closePx"):
            val = to_decimal(data.get(key))
            if val is not None and val > 0:
                return val
    return None


def units_for_amount(amount: Decimal, price: Decimal) -> int:
    if price <= 0:
        return 0
    return int((amount / price).to_integral_value(rounding=ROUND_DOWN))


def order_result(payload: Any, *, side: str, symbol: str) -> OrderResult:
    data = unwrap(payload)
    data = data if isinstance(data, dict) else {}
    queued = bool(data.get("queued"))
    return OrderResult(
        external_order_id=str(data.get("order_id") or data.get("queue_id") or "") or None,
        status="queued" if queued else str(data.get("status") or "submitted").lower(),
        side=side,
        symbol=symbol,
        message=(payload.get("message") if isinstance(payload, dict) else None),
        raw={k: data.get(k) for k in ("order_id", "queued", "queue_id", "next_open_at") if k in data} or None,
    )
