"""Pure parsers: InvestNaija (Zanibal) payloads -> normalized models."""

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
    OrderSide,
    PortfolioSummary,
)

WALLET_ACCOUNT_ID = "wallet:NGN"


def dec(v: Any) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    if isinstance(v, dict):
        # Zanibal money: {currency, amount}
        if "amount" in v:
            return dec(v.get("amount"))
        return None
    text = str(v).strip().replace(",", "").replace("₦", "")
    if not text or text.lower() in {"-", "—", "n/a", "null"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def money_amount(v: Any) -> Decimal | None:
    return dec(v)


def parse_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # epoch ms
        try:
            ts = float(value)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.startswith("-"):
        return None
    try:
        if len(text) == 10:
            return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def unwrap(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    if "data" in payload:
        return payload["data"]
    return payload


def broker_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    if isinstance(err, dict) and err.get("message"):
        msg = str(err["message"])
        # CHD sometimes stringifies a nested ProblemDetail JSON
        if msg.startswith("{") and "detail" in msg:
            try:
                nested = json_loads_safe(msg)
                if isinstance(nested, dict) and nested.get("detail"):
                    return str(nested["detail"])
            except Exception:
                pass
        return msg.strip()
    for key in ("message", "detail", "error"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def json_loads_safe(text: str) -> Any:
    import json

    return json.loads(text)


# --- auth ---------------------------------------------------------------


def login_tokens(payload: Any) -> dict[str, Any]:
    """Extract JWT + uuid_token from POST /auth/customers/login."""
    body = payload if isinstance(payload, dict) else {}
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    return {
        "token": body.get("token") or data.get("token"),
        "uuid_token": body.get("uuid_token") or data.get("uuid_token"),
        "two_factor": bool(data.get("twoFactorAuth")),
        "profile_id": data.get("id"),
        "email": data.get("email"),
        "message": body.get("message") or broker_message(body),
    }


# --- accounts / holdings ------------------------------------------------


def trade_portfolio_rows(payload: Any) -> list[dict[str, Any]]:
    data = unwrap(payload)
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("items", "portfolios", "result", "data"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
    return []


def account_from_trade_portfolio(row: dict[str, Any]) -> tuple[NormalizedAccount, dict[str, Any]]:
    pid = str(row.get("id") or row.get("portfolioId") or "")
    name = row.get("label") or row.get("portfolioName") or row.get("name") or "TradeIN"
    currency = "NGN"
    cv = row.get("currentValuation")
    if isinstance(cv, dict) and cv.get("currency"):
        currency = str(cv["currency"])
    elif row.get("currency"):
        currency = str(row["currency"])
    acct = NormalizedAccount(
        external_account_id=pid,
        account_name=str(name),
        account_type="brokerage",
        currency=currency,
    )
    meta = {
        "signature": row.get("signature"),
        "account_no": row.get("accountNo") or row.get("name"),
        "cash_account_id": row.get("cashAccountId"),
        "customer_id": row.get("customerId"),
        "trading_account_no": row.get("tradingAccountNo"),
        "security_exchange": row.get("securityExchange") or "NGX",
        "portfolio_type": row.get("portfolioType"),
        "available_cash": money_amount(row.get("availableCash")),
        "cash_balance": money_amount(row.get("cashBalance")),
    }
    return acct, meta


def wallet_account(balance: Decimal | None = None) -> NormalizedAccount:
    return NormalizedAccount(
        external_account_id=WALLET_ACCOUNT_ID,
        account_name="IN Wallet",
        account_type="cash",
        currency="NGN",
    )


def holdings_from_trade_portfolio(row: dict[str, Any], *, default_currency: str = "NGN") -> list[NormalizedHolding]:
    out: list[NormalizedHolding] = []
    for h in row.get("portfolioHoldings") or []:
        if not isinstance(h, dict):
            continue
        symbol = (h.get("symbol") or h.get("secId") or "").strip().upper()
        if not symbol:
            continue
        qty = dec(h.get("quantity"))
        if qty is None or qty == 0:
            continue
        px = dec(h.get("marketPrice") or h.get("marketPriceLC"))
        cost = dec(h.get("unitCost") or h.get("investedAmount"))
        currency = h.get("currency") or default_currency
        market_value = (qty * px) if px is not None else dec(h.get("investedAmount"))
        out.append(
            NormalizedHolding(
                symbol=symbol,
                security_name=str(h.get("companyName") or h.get("name") or symbol),
                quantity=qty,
                current_price=px,
                market_value=market_value,
                currency=str(currency) if currency else default_currency,
                average_cost=cost,
                cost_basis=(cost * qty) if cost is not None else dec(h.get("investedAmount")),
                asset_type=(h.get("assetClass") or "equity").lower(),
                security_identifier=str(h["securityId"]) if h.get("securityId") else None,
            )
        )
    return out


def holdings_from_detail(payload: Any, *, default_currency: str = "NGN") -> list[NormalizedHolding]:
    data = unwrap(payload)
    if not isinstance(data, dict):
        return []
    return holdings_from_trade_portfolio(data, default_currency=default_currency or data.get("currency") or "NGN")


def cash_from_trade_row(row: dict[str, Any]) -> Decimal | None:
    for key in ("availableCash", "cashBalance"):
        val = money_amount(row.get(key))
        if val is not None:
            return val
    return None


def cash_from_detail(payload: Any) -> Decimal | None:
    data = unwrap(payload)
    if not isinstance(data, dict):
        return None
    for key in ("cashBalance", "purchasingPower"):
        val = money_amount(data.get(key))
        if val is not None:
            return val
    return None


def cash_from_wallet_balance(payload: Any) -> Decimal | None:
    if not isinstance(payload, dict):
        return None
    if "balance" in payload:
        return dec(payload.get("balance"))
    data = unwrap(payload)
    if isinstance(data, dict) and "balance" in data:
        return dec(data.get("balance"))
    return None


def summary_from_trade_row(row: dict[str, Any], *, cash: Decimal | None) -> PortfolioSummary:
    currency = "NGN"
    cv = row.get("currentValuation")
    mv = money_amount(cv)
    if isinstance(cv, dict) and cv.get("currency"):
        currency = str(cv["currency"])
    elif row.get("currency"):
        currency = str(row["currency"])
    return PortfolioSummary(
        total_market_value=mv,
        cash_value=cash,
        currency=currency,
    )


def summary_from_detail(payload: Any, *, cash: Decimal | None) -> PortfolioSummary | None:
    data = unwrap(payload)
    if not isinstance(data, dict):
        return None
    return PortfolioSummary(
        total_market_value=dec(data.get("totalPortfolioValue")),
        cash_value=cash if cash is not None else dec(data.get("cashBalance")),
        currency=str(data.get("currency") or "NGN"),
    )


def transactions_from_wallet(payload: Any) -> list[NormalizedTransaction]:
    data = unwrap(payload)
    rows = data if isinstance(data, list) else []
    if isinstance(data, dict):
        for key in ("items", "result", "transactions", "data"):
            if isinstance(data.get(key), list):
                rows = data[key]
                break
    out: list[NormalizedTransaction] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ext = row.get("id") or row.get("reference") or row.get("gatewayReference")
        if ext is None:
            continue
        amount = dec(row.get("amount"))
        if amount is None:
            continue
        tx_type = (row.get("type") or row.get("channel") or "transfer").lower()
        if (row.get("type") or "").lower() == "debit":
            amount = -abs(amount)
        elif (row.get("type") or "").lower() == "credit":
            amount = abs(amount)
        out.append(
            NormalizedTransaction(
                external_transaction_id=str(ext),
                transaction_type=tx_type,
                total_amount=amount,
                currency=row.get("currency") or "NGN",
                transaction_date=parse_date(row.get("post_date") or row.get("createdAt") or row.get("date")),
            )
        )
    return out


# --- deposits -----------------------------------------------------------


def deposit_instructions_from_wallet(payload: Any) -> DepositInstructions:
    """Wallet balance payload includes vNuban + virtualAccounts (Wema)."""
    if not isinstance(payload, dict):
        raise ValueError("unexpected wallet payload")
    nuban = payload.get("vNuban")
    bank = None
    account_name = None
    va_id = None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    # walk saveplan_users -> virtualAccounts
    users = data.get("saveplan_users") if isinstance(data, dict) else None
    if isinstance(users, list):
        for user in users:
            if not isinstance(user, dict):
                continue
            vas = user.get("virtualAccounts") or []
            if not isinstance(vas, list):
                continue
            for va in vas:
                if not isinstance(va, dict):
                    continue
                if (va.get("status") or "").lower() not in {"", "active"}:
                    # still accept Active
                    if (va.get("status") or "").lower() != "active":
                        continue
                nuban = nuban or va.get("nuban")
                bank = bank or va.get("provider")
                va_id = va.get("id")
                if nuban:
                    break
            if nuban and bank:
                break
    if not nuban:
        raise LookupError("no InvestNaija wallet virtual account (NUBAN) issued yet")
    return DepositInstructions(
        bank_name=bank or "Wema Bank",
        account_number=str(nuban),
        account_name=account_name,
        currency="NGN",
        provider_reference=str(va_id) if va_id else None,
        raw={
            "credits": "IN Wallet (transfer to TradeIN in the InvestNaija app)",
            "source": "transactions/wallet/balance",
            "balance": payload.get("balance"),
        },
    )


# --- instruments / orders -----------------------------------------------


def securities_rows(payload: Any) -> list[dict[str, Any]]:
    data = unwrap(payload)
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("items", "securities", "result", "content"):
            if isinstance(data.get(key), list):
                return [r for r in data[key] if isinstance(r, dict)]
    return []


def instrument_from_security(row: dict[str, Any]) -> MarketInstrument | None:
    symbol = (row.get("symbol") or row.get("secId") or row.get("name") or "").strip().upper()
    if not symbol:
        return None
    return MarketInstrument(
        symbol=symbol,
        name=row.get("secDesc") or row.get("description") or row.get("name"),
        market_id=row.get("marketCode") or "NGX",
        exchange_id=row.get("marketCode") or "NGX",
        currency=row.get("currency") or "NGN",
        last_price=dec(row.get("lastPx") or row.get("price") or row.get("close")),
        asset_type=(row.get("type") or "equity").lower(),
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
        if row.get("status") and str(row.get("status")).upper() not in {"ACTIVE", "OPEN"}:
            continue
        if mid and (row.get("marketCode") or "NGX").upper() != mid:
            continue
        blob = f"{row.get('symbol') or ''} {row.get('secId') or ''} {row.get('secDesc') or ''}".lower()
        if term and term not in blob:
            continue
        inst = instrument_from_security(row)
        if inst:
            filtered.append(inst)
    filtered.sort(key=lambda i: i.symbol)
    page = max(1, int(page))
    per_page = max(1, min(int(per_page), 500))
    start = (page - 1) * per_page
    return InstrumentListResult(
        market_id=mid or "NGX",
        page=page,
        per_page=per_page,
        total=len(filtered),
        instruments=filtered[start : start + per_page],
    )


def day_order_term(payload: Any) -> dict[str, Any] | None:
    data = unwrap(payload)
    rows: list[Any] = []
    if isinstance(data, dict):
        rows = data.get("result") or data.get("items") or []
    elif isinstance(data, list):
        rows = data
    for row in rows:
        if isinstance(row, dict) and "DAY" in str(row.get("label") or "").upper():
            return row
    return rows[0] if rows and isinstance(rows[0], dict) else None


def order_result_from_response(payload: Any, *, side: str, symbol: str) -> OrderResult:
    data = unwrap(payload)
    data = data if isinstance(data, dict) else {}
    order_id = data.get("id") or data.get("orderId") or data.get("orderNo") or data.get("reference")
    status = data.get("status") or data.get("orderStatus") or ("submitted" if payload is not None else "unknown")
    try:
        side_enum = OrderSide(side.lower())
    except ValueError:
        side_enum = None
    return OrderResult(
        external_order_id=str(order_id) if order_id is not None else None,
        status=str(status).lower(),
        side=side_enum,
        symbol=data.get("symbol") or data.get("securityName") or symbol,
        filled_qty=dec(data.get("filledQty") or data.get("quantity")),
        message=broker_message(payload) if isinstance(payload, dict) else None,
        raw=data or None,
    )
