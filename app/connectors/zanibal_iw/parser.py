"""IW page -> normalized models. Column lookup is by header text (table ids vary per deployment)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

from app.connectors.models import (
    InstrumentListResult,
    MarketInstrument,
    NormalizedHolding,
    NormalizedTransaction,
    PortfolioSummary,
)

from .html import Page, Table

_DATE_FORMATS = ("%d-%b-%Y %H:%M:%S.%f", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d")


def num(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("₦", "").replace("%", "").strip()
    if not text or text.lower() in {"-", "—", "nan", "n/a", "null"}:
        return None
    neg = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        d = Decimal(text)
    except InvalidOperation:
        return None
    return -d if neg else d


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _cell(row: list[str], table: Table, name: str) -> str | None:
    i = table.col(name)
    return row[i] if i is not None and i < len(row) else None


def _is_total(row: list[str]) -> bool:
    return bool(row) and row[0].strip().lower().startswith("total")


# --- session / identity -------------------------------------------------


def is_login_page(page: Page) -> bool:
    return any("Password" in f.inputs and "UserName" in f.inputs for f in page.forms)


def is_authenticated(page: Page) -> bool:
    t = page.text
    return ("Log Off" in t or "Logout" in t or "Log Out" in t) and not is_login_page(page)


def needs_terms(page: Page) -> bool:
    return "Terms and Conditions" in page.text and any("SubmitTerms" in (f.action or "") for f in page.forms) and \
        "Customer Number" not in page.text


def mfa_form(page: Page):
    for f in page.forms:
        for name in f.inputs:
            low = name.lower()
            if any(k in low for k in ("otp", "token", "code", "pin")) and "requestverification" not in low:
                return f, name
    return None, None


def customer_number(page: Page) -> str | None:
    m = re.search(r"Customer Number:\s*([A-Za-z0-9\-/]+)", page.text)
    return m.group(1) if m else None


def login_error(page: Page) -> str | None:
    """First alert that is not a client-side 'field is required' hint."""
    for a in page.alerts:
        if a and "field is required" not in a.lower():
            return a
    return None


# --- cash / accounts ----------------------------------------------------


def master_accounts(page: Page) -> list[tuple[str, str]]:
    form = page.form_with_field("MasterAccount")
    if not form:
        return []
    seen: dict[str, str] = {}
    for value, text, _ in form.selects["MasterAccount"].options:
        if value and value not in seen:
            seen[value] = text
    return list(seen.items())


def statement_total(page: Page) -> Decimal | None:
    for t in page.tables:
        for row in t.rows:
            if len(row) >= 2 and row[0].lower().startswith("total balance"):
                return num(row[1])
    return None


def trade_balances(page: Page) -> dict[str, Decimal | None]:
    """'Deposit for shares account balance', 'Open Orders', 'Balance Available for Purchases'."""
    out: dict[str, Decimal | None] = {"cash": None, "open_orders": None, "available": None}
    t = page.table_with("account balance")
    if not t:
        return out
    hdr = " | ".join(t.headers)
    m = re.search(r"account balance \| ([\d,().\-]+)", hdr, re.I)
    if m:
        out["cash"] = num(m.group(1))
    for row in t.rows:  # live markup: <tr><th>Label</th><td>value</td></tr>
        if len(row) < 2:
            continue
        label = row[0].lower()
        if "open orders" in label:
            out["open_orders"] = num(row[1])
        elif "available" in label:
            out["available"] = num(row[1])
    return out


# --- holdings -----------------------------------------------------------


def holdings_from_summary(page: Page, *, currency: str = "NGN") -> list[NormalizedHolding]:
    """'Summary Per Stock' table: Symbol, Name, Units, Avg Unit Price, Mkt. Value, Price, Cummulative Cost, Gain."""
    t = page.table_with("Symbol", "Units", "Mkt. Value")
    if not t:
        return []
    out: list[NormalizedHolding] = []
    for row in t.rows:
        if _is_total(row) or len(row) < 3:
            continue
        sym = (_cell(row, t, "Symbol") or "").strip() or None
        name = (_cell(row, t, "Name") or "").strip() or sym
        if not name:
            continue
        units = num(_cell(row, t, "Units"))
        cost = num(_cell(row, t, "Cummulative Cost") or _cell(row, t, "Cumulative Cost"))
        mv = num(_cell(row, t, "Mkt. Value"))
        gain = num(_cell(row, t, "Gain"))
        if gain is None and mv is not None and cost is not None:
            gain = mv - cost
        out.append(NormalizedHolding(
            symbol=sym, security_name=name, asset_type="equity", quantity=units,
            average_cost=num(_cell(row, t, "Avg Unit Price")), cost_basis=cost,
            current_price=num(_cell(row, t, "Price")),
            market_value=mv, unrealized_pnl=gain, currency=currency,
        ))
    return out


def equities_from_holdings_page(page: Page, *, currency: str = "NGN") -> list[NormalizedHolding]:
    """Fallback when Summary Per Stock is unavailable: Security Name, Quantity, Market Value."""
    t = next((x for x in page.tables_with("Security Name", "Quantity", "Market Value")), None)
    if not t:
        return []
    out = []
    for row in t.rows:
        if _is_total(row):
            continue
        name = (_cell(row, t, "Security Name") or "").strip()
        if not name:
            continue
        out.append(NormalizedHolding(security_name=name, symbol=name if re.fullmatch(r"[A-Z0-9]{2,15}", name) else None,
                                     asset_type="equity", quantity=num(_cell(row, t, "Quantity")),
                                     market_value=num(_cell(row, t, "Market Value")), currency=currency))
    return out


def fixed_income_from_holdings_page(page: Page, *, currency: str = "NGN") -> list[NormalizedHolding]:
    """Treasury Bills / Bonds tables: Trans ID, Security Name, Discounted Value, Face Value."""
    out = []
    for t in page.tables_with("Trans ID", "Security Name", "Face Value"):
        kind = "treasury_bill" if "bill" in t.heading.lower() else "bond"
        for row in t.rows:
            if _is_total(row):
                continue
            name = (_cell(row, t, "Security Name") or "").strip()
            if not name:
                continue
            tid = (_cell(row, t, "Trans ID") or "").strip()
            out.append(NormalizedHolding(
                security_name=name, asset_type=kind,
                quantity=num(_cell(row, t, "Face Value")),  # face value as nominal quantity
                market_value=num(_cell(row, t, "Discounted Value")),
                currency=currency,
                security_identifier=f"iw:{kind}:{tid or name}",
            ))
    return out


def summary_totals(page: Page, *, cash: Decimal | None) -> PortfolioSummary | None:
    t = page.table_with("Summary", "Value")
    if not t:
        return None
    vals = {r[0].strip().lower(): num(r[1]) for r in t.rows if len(r) >= 2}
    return PortfolioSummary(
        total_market_value=vals.get("total market value"),
        total_cost_basis=vals.get("total cost"),
        unrealized_pnl=vals.get("total gain/loss"),
        cash_value=cash,
        currency="NGN",
        valuation_source="broker_supplied",
    )


# --- transactions -------------------------------------------------------


def cash_transactions(page: Page, *, account: str) -> list[NormalizedTransaction]:
    t = page.table_with("Value Date", "Description", "Debit", "Credit")
    if not t:
        return []
    out = []
    for idx, row in enumerate(t.rows):
        desc = (_cell(row, t, "Description") or "").strip()
        if not desc or desc.lower() in {"initial balance", "opening balance", "closing balance"}:
            continue
        debit = num(_cell(row, t, "Debit")) or Decimal("0")
        credit = num(_cell(row, t, "Credit")) or Decimal("0")
        date = _cell(row, t, "Value Date")
        out.append(NormalizedTransaction(
            external_transaction_id=f"cash:{account}:{date}:{idx}:{desc[:40]}",
            transaction_type="credit" if credit and not debit else "debit" if debit and not credit else "cash",
            total_amount=credit - debit, currency="NGN", transaction_date=parse_date(date),
        ))
    return out


def portfolio_transactions(page: Page, *, account: str) -> list[NormalizedTransaction]:
    t = page.table_with("Date", "Type", "Stock Code", "Units")
    if not t:
        return []
    out = []
    for idx, row in enumerate(t.rows):
        if _is_total(row):
            continue
        sym = (_cell(row, t, "Stock Code") or "").strip() or None
        typ = (_cell(row, t, "Type") or "").strip().lower() or "trade"
        date = _cell(row, t, "Date")
        proceeds = num(_cell(row, t, "Proceeds"))
        if proceeds is not None and typ.startswith("b"):
            proceeds = -abs(proceeds)
        out.append(NormalizedTransaction(
            external_transaction_id=f"sec:{account}:{date}:{sym}:{idx}",
            transaction_type=typ, symbol=sym, quantity=num(_cell(row, t, "Units")),
            unit_price=num(_cell(row, t, "Unit Price")), fees=num(_cell(row, t, "Fees")),
            total_amount=proceeds, currency="NGN", transaction_date=parse_date(date),
        ))
    return out


# --- instruments / trading ----------------------------------------------


def instruments_from_market_stats(page: Page, *, search: str | None, page_no: int, per_page: int) -> InstrumentListResult:
    t = page.table_with("Symbol", "Close", "Volume")
    term = (search or "").strip().lower()
    items: list[MarketInstrument] = []
    for row in (t.rows if t else []):
        sym = (_cell(row, t, "Symbol") or "").strip()
        if not sym or (term and term not in sym.lower()):
            continue
        items.append(MarketInstrument(symbol=sym, market_id="NGX", exchange_id="NGX", currency="NGN",
                                      last_price=num(_cell(row, t, "Close")), provider_instrument_id=sym))
    items.sort(key=lambda i: i.symbol)
    page_no, per_page = max(1, page_no), max(1, min(per_page, 500))
    start = (page_no - 1) * per_page
    return InstrumentListResult(market_id="NGX", page=page_no, per_page=per_page, total=len(items),
                                instruments=items[start:start + per_page])


def last_trade_price(body: str) -> Decimal | None:
    """/Trade/LastTrade returns a JSON string containing a JSON array (double-encoded)."""
    try:
        data = json.loads(body)
        if isinstance(data, str):
            data = json.loads(data)
    except (ValueError, TypeError):
        return None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return num(data[0].get("LastPrice"))
    return None


def units_for_amount(amount: Decimal, price: Decimal) -> int:
    return int((amount / price).to_integral_value(rounding=ROUND_DOWN)) if price > 0 else 0
