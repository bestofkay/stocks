"""Pure parsers: CardinalStone JSON payloads -> normalized models.

Unknown / missing numbers stay ``None`` (never coerced to zero). An empty list
returned from a successful API call means the account is confirmed empty.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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


def parse_date(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith("-"):  # e.g. "-999999999-01-01" sentinel
        return None
    try:
        if len(text) == 10:
            return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def unwrap(payload: Any) -> Any:
    """API wraps most responses as ``{statusCode, message, data}``."""
    if isinstance(payload, dict) and "data" in payload and set(payload) <= {
        "data", "statusCode", "message", "error", "success", "status",
    }:
        return payload["data"]
    return payload


def broker_message(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("message", "error", "details"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, list) and val:
                return "; ".join(str(v) for v in val)
    return None


# --- auth ---------------------------------------------------------------


def login_tokens(payload: Any) -> dict[str, Any]:
    """Extract tokens + non-secret user ids from a /auth/login response."""
    body = payload if isinstance(payload, dict) else {}
    if not body.get("token") and isinstance(body.get("data"), dict):
        body = body["data"]
    user = body.get("user") if isinstance(body.get("user"), dict) else {}
    return {
        "token": body.get("token"),
        "refresh_token": body.get("refresh_token"),
        "maintenance": body.get("maintenanceMode") is True,
        "cust_id": str(user["CustID"]) if user.get("CustID") is not None else None,
        "client_id": user.get("SEC_ID"),
        "is_2fa_enabled": bool(user.get("is2FAEnabled")),
    }


def client_ids_from_profile(payload: Any) -> dict[str, str | None]:
    data = unwrap(payload)
    if not isinstance(data, dict):
        return {"cust_id": None, "client_id": None}
    cust = data.get("CustID")
    return {
        "cust_id": str(cust) if cust is not None else None,
        "client_id": data.get("SEC_ID"),
    }


# --- accounts / holdings ------------------------------------------------


def accounts_from_investment_accounts(payload: Any) -> list[tuple[NormalizedAccount, dict[str, Any]]]:
    """Return ``(account, meta)`` pairs; meta keeps ids needed for orders."""
    rows = unwrap(payload)
    if not isinstance(rows, list):
        return []
    out: list[tuple[NormalizedAccount, dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        if row.get("deleted") is True:
            continue
        label = row.get("productLabel") or row.get("productCode") or "Investment account"
        acct_no = row.get("accountNo")
        out.append(
            (
                NormalizedAccount(
                    external_account_id=str(row["id"]),
                    account_name=f"{label} ({acct_no})" if acct_no else str(label),
                    account_type=row.get("productCode"),
                    currency=row.get("currency") or "NGN",
                    cash_balance=to_decimal(row.get("cashBalance")),
                ),
                {
                    "trading_account_no": row.get("tradingAccountNo"),
                    "product_code": row.get("productCode"),
                    "status": row.get("status"),
                },
            )
        )
    # NGX trading first (mirrors the web app ordering)
    out.sort(key=lambda pair: 0 if pair[1].get("product_code") == "NGX_TRADING" else 1)
    return out


def wallet_account(balance: Decimal | None) -> NormalizedAccount:
    return NormalizedAccount(
        external_account_id=WALLET_ACCOUNT_ID,
        account_name="NGN Wallet",
        account_type="WALLET",
        currency="NGN",
        cash_balance=balance,
    )


def cash_from_wallet_balance(payload: Any) -> Decimal | None:
    data = unwrap(payload)
    if isinstance(data, dict):
        return to_decimal(data.get("balance"))
    return None


def cash_from_account_details(payload: Any) -> Decimal | None:
    data = unwrap(payload)
    if isinstance(data, dict):
        return to_decimal(data.get("currentBalance"))
    return None


def holdings_from_trading_report(
    payload: Any,
    *,
    default_currency: str = "NGN",
    names: dict[str, dict[str, Any]] | None = None,
) -> list[NormalizedHolding]:
    data = unwrap(payload)
    if not isinstance(data, dict):
        return []
    currency = data.get("reportCurrency") or default_currency
    as_of = parse_date(data.get("positionDate"))
    names = names or {}
    holdings: list[NormalizedHolding] = []
    for row in data.get("positionInstruments") or []:
        if not isinstance(row, dict):
            continue
        sec_id = (row.get("secId") or "").strip() or None
        ref = names.get(sec_id or "", {})
        name = row.get("secDesc") or row.get("secLabel") or ref.get("secDesc") or sec_id
        if not name:
            continue
        qty = to_decimal(row.get("quantity"))
        total_cost = to_decimal(row.get("totalCost"))
        value = to_decimal(row.get("currentValue"))
        pnl = to_decimal(row.get("gainLoss"))
        if pnl is None and value is not None and total_cost is not None:
            pnl = value - total_cost
        holdings.append(
            NormalizedHolding(
                symbol=sec_id,
                security_name=str(name),
                isin=row.get("isin") or ref.get("isin"),
                asset_type=(row.get("assetType") or ref.get("type") or "EQUITY").lower(),
                quantity=qty,
                average_cost=to_decimal(row.get("avgCost")),
                cost_basis=total_cost,
                current_price=to_decimal(row.get("currentPrice")),
                market_value=value,
                unrealized_pnl=pnl,
                currency=currency,
                valuation_timestamp=as_of,
            )
        )
    return holdings


def summary_from_trading_report(payload: Any, *, cash: Decimal | None = None) -> PortfolioSummary | None:
    data = unwrap(payload)
    if not isinstance(data, dict) or "totalValue" not in data:
        return None
    return PortfolioSummary(
        total_market_value=to_decimal(data.get("totalValue")),
        total_cost_basis=to_decimal(data.get("totalCost")),
        unrealized_pnl=to_decimal(data.get("gainLoss")),
        cash_value=cash,
        currency=data.get("reportCurrency") or "NGN",
        valuation_timestamp=parse_date(data.get("positionDate")),
        valuation_source="broker_supplied",
    )


def transactions_from_statement(payload: Any) -> list[NormalizedTransaction]:
    data = unwrap(payload)
    if not isinstance(data, dict):
        return []
    currency = data.get("accountCurrency") or data.get("currency")
    out: list[NormalizedTransaction] = []
    for idx, line in enumerate(data.get("statementLines") or []):
        if not isinstance(line, dict):
            continue
        if (line.get("entryType") or "").upper() == "BALANCE":
            continue  # opening / closing balance rows
        credit = to_decimal(line.get("credit")) or Decimal("0")
        debit = to_decimal(line.get("debit")) or Decimal("0")
        ext = line.get("journalId") or line.get("journalNo") or (
            f"{line.get('refNo') or 'line'}:{line.get('tranDate')}:{idx}"
        )
        out.append(
            NormalizedTransaction(
                external_transaction_id=str(ext),
                transaction_type=(line.get("entryType") or ("credit" if credit else "debit")).lower(),
                total_amount=credit - debit,
                currency=line.get("transCurrency") or currency,
                transaction_date=parse_date(line.get("valueDate") or line.get("tranDate")),
            )
        )
    return out


# --- deposits -----------------------------------------------------------


def deposit_instructions_from_nuban(payload: Any, *, currency: str = "NGN") -> DepositInstructions:
    rows = unwrap(payload)
    if not isinstance(rows, list):
        raise ValueError("unexpected NUBAN payload")
    candidates = [
        r for r in rows
        if isinstance(r, dict) and r.get("accountNumber") and not r.get("deleted") and not r.get("suspended")
    ]
    if not candidates:
        raise LookupError("no funding account issued yet")
    pick = next((r for r in candidates if (r.get("currency") or "").upper() == currency), candidates[0])
    product = pick.get("nubanProduct") if isinstance(pick.get("nubanProduct"), dict) else {}
    return DepositInstructions(
        bank_name=pick.get("bankName"),
        account_number=str(pick["accountNumber"]),
        account_name=pick.get("accountName"),
        currency=pick.get("currency") or currency,
        provider_reference=pick.get("accountRef"),
        raw={
            "gateway": pick.get("gateway"),
            "product": product.get("name"),
            "credits": "NGN Wallet",
        },
    )


# --- instruments / orders -----------------------------------------------


def equities_page(payload: Any) -> tuple[list[dict[str, Any]], int]:
    data = unwrap(payload)
    if not isinstance(data, dict):
        return [], 0
    rows = [r for r in data.get("content") or [] if isinstance(r, dict)]
    try:
        pages = int(data.get("totalPages") or 0)
    except (TypeError, ValueError):
        pages = 0
    return rows, pages


def instrument_from_equity(row: dict[str, Any]) -> MarketInstrument | None:
    sec_id = (row.get("secId") or "").strip()
    if not sec_id:
        return None
    return MarketInstrument(
        symbol=sec_id,
        name=row.get("secDesc"),
        market_id=row.get("marketCode"),
        exchange_id=row.get("marketCode"),
        currency=row.get("currency"),
        last_price=to_decimal(row.get("price")),
        asset_type=(row.get("type") or "").lower() or None,
        provider_instrument_id=row.get("id"),
    )


def instruments_result(
    rows: list[dict[str, Any]],
    *,
    market_id: str | None,
    search: str | None,
    page: int,
    per_page: int,
) -> InstrumentListResult:
    term = (search or "").strip().lower()
    mid = (market_id or "").strip().upper()
    filtered: list[MarketInstrument] = []
    for row in rows:
        if row.get("status") and row.get("status") != "ACTIVE":
            continue
        if mid and (row.get("marketCode") or "").upper() != mid:
            continue
        if term and term not in (row.get("secId") or "").lower() and term not in (row.get("secDesc") or "").lower():
            continue
        inst = instrument_from_equity(row)
        if inst:
            filtered.append(inst)
    filtered.sort(key=lambda i: i.symbol)
    page = max(1, int(page))
    per_page = max(1, min(int(per_page), 500))
    start = (page - 1) * per_page
    return InstrumentListResult(
        market_id=mid or None,
        page=page,
        per_page=per_page,
        total=len(filtered),
        instruments=filtered[start : start + per_page],
    )


def qty_from_calculate(payload: Any) -> Decimal | None:
    data = unwrap(payload)
    if isinstance(data, dict):
        for key in ("requestedQty", "quantity", "qty", "units", "orderQty"):
            val = to_decimal(data.get(key))
            if val is not None and val > 0:
                return val
    return None


def order_result_from_response(payload: Any, *, side: str, symbol: str) -> OrderResult:
    data = unwrap(payload)
    data = data if isinstance(data, dict) else {}
    order_id = data.get("id") or data.get("orderId") or data.get("orderNo")
    status = data.get("orderStatus") or data.get("status") or ("submitted" if payload is not None else "unknown")
    return OrderResult(
        external_order_id=str(order_id) if order_id is not None else None,
        status=str(status).lower(),
        side=side,
        symbol=data.get("secId") or symbol,
        filled_qty=to_decimal(data.get("cumQty") or data.get("filledQty")),
        message=broker_message(payload),
        raw={
            k: data.get(k)
            for k in ("id", "orderNo", "orderStatus", "routingStatus", "routingMessage", "requestedQty", "limitPrice", "tif")
            if k in data
        }
        or None,
    )
