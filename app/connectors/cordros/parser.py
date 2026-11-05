"""Pure parsers: Cordros CAPE / SymPlus payloads -> normalized models.

Gateway envelope: ``{success, message, data: {status, result: [{<Operation>: [rows]}]}}``;
``data`` is ``null`` when the backing record does not exist (e.g. no customer yet).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from html import unescape
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

from . import api_paths

CASH_ROW = "= CASH -"
TOTAL_ROW = "TOTAL -"
GRAND_TOTAL_ROW = "== GRAND TOTAL =="


def to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"-", "null", "nan"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    for fmt in (None, "%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.fromisoformat(text) if fmt is None else datetime.strptime(text, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def message(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for k in ("message", "error"):
            v = payload.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def gateway_rows(payload: Any, operation: str) -> list[dict[str, Any]] | None:
    """Rows for ``operation`` or ``None`` when SymPlus returned no data (record missing)."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if data is None:
        return None
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, list):
        return []
    key = operation.strip("/").split("/")[0]
    for block in result:
        if isinstance(block, dict) and key in block:
            rows = block[key]
            if isinstance(rows, dict):  # occasionally nested {result: [...]}
                rows = rows.get("result") or []
            return [r for r in rows if isinstance(r, dict)]
    return []


# --- auth / identity ----------------------------------------------------


def login_info(payload: Any) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    if not body.get("access_token") and isinstance(body.get("data"), dict):
        body = body["data"]
    user = body.get("user") if isinstance(body.get("user"), dict) else {}
    return {"access_token": body.get("access_token"), "user": user}


def customer_from_user(user: Any) -> dict[str, Any] | None:
    """Active customer record (KYC-approved). Mirrors the web app's selection."""
    if not isinstance(user, dict):
        return None
    cands = []
    if isinstance(user.get("customer"), dict):
        cands.append(user["customer"])
    for c in user.get("account_listing") or []:
        if isinstance(c, dict):
            cands.append(c.get("customer") if isinstance(c.get("customer"), dict) else c)
    for c in cands:
        if str(c.get("customerStatus") or "").lower() == "active" and (c.get("cscs") or c.get("stId")):
            return {
                "customer_id": str(c.get("id")) if c.get("id") is not None else None,
                "customer_code": c.get("customerCode"),
                "name": c.get("full_name"),
                "st_id": str(c["stId"]) if c.get("stId") is not None else None,
                "am_id": str(c["amId"]) if c.get("amId") is not None else None,
                "cscs": str(c["cscs"]) if c.get("cscs") is not None else None,
                "chn": c.get("chn"),
            }
    return None


def user_from_profile(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload if isinstance(payload, dict) else {}


# --- accounts / holdings ------------------------------------------------


def market_accounts(rows: list[dict[str, Any]] | None) -> list[tuple[NormalizedAccount, dict[str, Any]]]:
    out = []
    for r in rows or []:
        acc_id = r.get("MARKET_ACCOUNT_ID")
        if acc_id in (None, ""):
            continue
        desc = r.get("MARKET_DESCRIPTION") or "Stock trading account"
        out.append((
            NormalizedAccount(external_account_id=str(acc_id), account_name=f"{desc} ({acc_id})",
                              account_type="STOCKBROKING", currency="NGN"),
            {"market_id": r.get("MARKET_ID"), "cash_funding_account_id": r.get("CASH_FUNDING_ACCOUNT_ID")},
        ))
    return out


def _ident(row: dict[str, Any]) -> str:
    return str(row.get("SYMBOL_IDENTIFIER") or "")


def is_summary_row(row: dict[str, Any]) -> bool:
    ident = _ident(row).strip()
    return ident.startswith("=") or GRAND_TOTAL_ROW in ident or TOTAL_ROW in ident


def holdings_from_portfolio(rows: list[dict[str, Any]] | None) -> list[NormalizedHolding]:
    out = []
    for r in rows or []:
        if is_summary_row(r):
            continue
        sym = _ident(r).strip() or None
        name = r.get("SYMBOL_DESCRIPTION") or sym
        if not name:
            continue
        mv = to_decimal(r.get("MARKET_VALUE"))
        cost = to_decimal(r.get("AVERAGE_COST_VALUE"))
        out.append(NormalizedHolding(
            symbol=sym, security_name=str(name), asset_type="equity",
            quantity=to_decimal(r.get("UNIT_QUANTITY")),
            average_cost=to_decimal(r.get("AVERAGE_COST_PRICE")), cost_basis=cost,
            current_price=to_decimal(r.get("MARKET_PRICE")), market_value=mv,
            unrealized_pnl=(mv - cost) if mv is not None and cost is not None else None,
            currency="NGN",
        ))
    return out


def portfolio_totals(rows: list[dict[str, Any]] | None) -> dict[str, Decimal | None]:
    out: dict[str, Decimal | None] = {"cash": None, "assets": None, "grand_total": None}
    for r in rows or []:
        ident = _ident(r)
        if CASH_ROW in ident:
            out["cash"] = to_decimal(r.get("MARKET_VALUE"))
        elif GRAND_TOTAL_ROW in ident:
            out["grand_total"] = to_decimal(r.get("MARKET_VALUE"))
        elif TOTAL_ROW in ident:
            out["assets"] = to_decimal(r.get("MARKET_VALUE"))
    return out


def summary(rows: list[dict[str, Any]] | None, *, cash: Decimal | None) -> PortfolioSummary:
    h = holdings_from_portfolio(rows)
    tot = portfolio_totals(rows)
    costs = [x.cost_basis for x in h]
    cost = sum(costs, Decimal("0")) if h and all(c is not None for c in costs) else (Decimal("0") if not h else None)
    mv = tot["assets"] if tot["assets"] is not None else sum((x.market_value or Decimal("0") for x in h), Decimal("0"))
    return PortfolioSummary(
        total_market_value=mv, total_cost_basis=cost,
        unrealized_pnl=(mv - cost) if mv is not None and cost is not None else None,
        cash_value=cash if cash is not None else tot["cash"], currency="NGN", valuation_source="broker_supplied",
    )


def cash_balance(rows: list[dict[str, Any]] | None) -> Decimal | None:
    for r in rows or []:
        val = to_decimal(r.get("AVAILABLE_BALANCE"))
        if val is not None:
            return val
    return None


def trades(rows: list[dict[str, Any]] | None, *, account: str) -> list[NormalizedTransaction]:
    out = []
    for i, r in enumerate(rows or []):
        side = str(r.get("PURCHASE_SALE") or "").strip().upper()
        typ = "buy" if side.startswith("P") or side.startswith("B") else "sell" if side.startswith("S") else (side.lower() or "trade")
        net = to_decimal(r.get("NET_TRADE_VALUE"))
        if net is not None and typ == "buy":
            net = -abs(net)
        ext = r.get("TRADE_ID") or r.get("CONTRACT_NUMBER") or f"{r.get('TRADE_DATE')}:{r.get('TRADE_DESCRIPTION')}:{i}"
        out.append(NormalizedTransaction(
            external_transaction_id=f"trade:{account}:{ext}", transaction_type=typ,
            symbol=r.get("SYMBOL_CODE") or None, quantity=to_decimal(r.get("TRADE_VOLUME")),
            unit_price=to_decimal(r.get("TRADE_PRICE")), total_amount=net, currency="NGN",
            transaction_date=parse_dt(r.get("TRADE_DATE")),
        ))
    return out


def customer_txns(payload: Any) -> list[NormalizedTransaction]:
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get("data") if isinstance(data, dict) else data
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        out.append(NormalizedTransaction(
            external_transaction_id=f"cust:{r.get('id') or r.get('reference') or r.get('created_at')}",
            transaction_type=str(r.get("transactionaction") or r.get("type") or "transaction").lower(),
            quantity=to_decimal(r.get("quantity")), total_amount=to_decimal(r.get("amount")),
            currency=r.get("currency") or "NGN", transaction_date=parse_dt(r.get("created_at")),
        ))
    return out


# --- deposits -----------------------------------------------------------


def deposit_from_config(payload: Any) -> DepositInstructions:
    data = payload.get("data") if isinstance(payload, dict) else None
    html = (data or {}).get("payment_info") if isinstance(data, dict) else None
    if not html:
        raise LookupError("no payment instructions configured")
    text = re.sub(r"<br\s*/?>|</p>|</div>|</span>|</strong>", "\n", html, flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", "", text)).replace("\xa0", " ")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    idx = next((i for i, ln in enumerate(lines) if re.fullmatch(r"\d{10}", ln)), None)
    if idx is None:
        raise ValueError("no 10-digit account number in payment instructions")
    name = lines[idx - 1] if idx > 0 else None
    bank = lines[idx + 1] if idx + 1 < len(lines) else None
    notes = [ln for ln in lines if "third" in ln.lower() or "3rd" in ln.lower() or "exact amount" in ln.lower()]
    return DepositInstructions(
        bank_name=bank, account_number=lines[idx], account_name=name, currency="NGN",
        raw={"kind": "shared_collection_account", "notes": notes,
             "after_transfer": "Notify Cordros in the web app ('I have paid') so the deposit is matched."},
    )


# --- instruments / orders -----------------------------------------------


def instruments(rows: list[dict[str, Any]] | None, *, search: str | None, page: int, per_page: int) -> InstrumentListResult:
    term = (search or "").strip().lower()
    items = []
    for r in rows or []:
        sym = str(r.get("SYMBOL_CODE") or "").strip()
        mkt = str(r.get("MARKET_CODE") or "")
        if not sym or (mkt and not mkt.startswith("XNSA")):
            continue
        desc = r.get("SYMBOL_DESCRIPTION")
        if term and term not in sym.lower() and term not in str(desc or "").lower():
            continue
        items.append(MarketInstrument(
            symbol=sym, name=desc, market_id=api_paths.DEFAULT_MARKET, exchange_id=mkt or None, currency="NGN",
            last_price=to_decimal(r.get("CURRENT_PRICE")),
            asset_type="etf" if mkt.endswith("ETF") else "equity", provider_instrument_id=sym,
        ))
    items.sort(key=lambda i: i.symbol)
    page, per_page = max(1, page), max(1, min(per_page, 500))
    start = (page - 1) * per_page
    return InstrumentListResult(market_id=api_paths.DEFAULT_MARKET, page=page, per_page=per_page,
                                total=len(items), instruments=items[start:start + per_page])


def price_from_rows(rows: list[dict[str, Any]] | None) -> Decimal | None:
    for r in rows or []:
        for k in ("CURRENT_PRICE", "PREV_CLOSE_PRICE"):
            v = to_decimal(r.get(k))
            if v is not None and v > 0:
                return v
    return None


def units_for_amount(amount: Decimal, price: Decimal) -> int:
    return int((amount / price).to_integral_value(rounding=ROUND_DOWN)) if price > 0 else 0


def order_result(payload: Any, *, side: str, symbol: str) -> OrderResult:
    data = payload.get("data") if isinstance(payload, dict) else None
    oid = None
    rows = gateway_rows(payload, "DoBuyOrder") or gateway_rows(payload, "DoSellOrder") or []
    for r in rows:
        oid = r.get("ORDER_IDENTIFIER") or r.get("ORDER_ID") or oid
    return OrderResult(
        external_order_id=str(oid) if oid else None, status="submitted", side=side, symbol=symbol,
        message=message(payload), raw={"data": data} if isinstance(data, (dict, list)) else None,
    )
