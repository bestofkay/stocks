"""Parse Afrinvestor HTML/text into normalized holdings.

Uses saved HTML fixtures for unit tests. Live selectors remain unverified.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any

from app.connectors.models import (
    DepositInstructions,
    InstrumentListResult,
    MarketInstrument,
    NormalizedAccount,
    NormalizedHolding,
    OrderResult,
    OrderSide,
)


def parse_decimal(value: str | None) -> Decimal | None:
    """Parse a localized number string into Decimal. Returns None if unknown."""
    if value is None:
        return None
    text = value.strip()
    if not text or text in {"-", "—", "N/A", "n/a", "null"}:
        return None
    # Remove currency symbols and whitespace
    text = re.sub(r"[₦$£€,\s]", "", text)
    # Handle parentheses as negative
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_currency_hint(text: str) -> str | None:
    upper = text.upper()
    if "₦" in text or "NGN" in upper:
        return "NGN"
    if "$" in text or "USD" in upper:
        return "USD"
    if "£" in text or "GBP" in upper:
        return "GBP"
    if "€" in text or "EUR" in upper:
        return "EUR"
    return None


class _TableExtractor(HTMLParser):
    """Minimal HTML table extractor for fixture-based tests."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._current_table = []
        elif tag == "tr" and self._current_table is not None:
            self._current_row = []
        elif tag in {"td", "th"} and self._current_row is not None:
            self._current_cell = []
            self._in_cell = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._in_cell and self._current_row is not None:
            cell = "".join(self._current_cell or []).strip()
            self._current_row.append(cell)
            self._current_cell = None
            self._in_cell = False
        elif tag == "tr" and self._current_row is not None and self._current_table is not None:
            if self._current_row:
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._current_table is not None:
            self.tables.append(self._current_table)
            self._current_table = None

    def handle_data(self, data: str) -> None:
        if self._in_cell and self._current_cell is not None:
            self._current_cell.append(data)


def extract_tables(html: str) -> list[list[list[str]]]:
    parser = _TableExtractor()
    parser.feed(html)
    return parser.tables


def holdings_from_table(
    rows: list[list[str]],
    *,
    default_currency: str | None = "NGN",
    valuation_timestamp: datetime | None = None,
) -> list[NormalizedHolding]:
    """Map a simple holdings table to NormalizedHolding list.

    Expected header columns (case-insensitive, flexible order):
      Symbol / Ticker, Name / Security, Quantity / Qty, Price, Value / Market Value,
      Avg Cost / Average Cost, Cost Basis, P/L / Unrealized
    """
    if not rows:
        return []

    header = [c.strip().lower() for c in rows[0]]
    col = _column_map(header)
    if "name" not in col and "symbol" not in col:
        # No recognizable header — treat as data-only if first row looks like data
        return []

    holdings: list[NormalizedHolding] = []
    ts = valuation_timestamp or datetime.now(timezone.utc)

    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue

        def cell(key: str) -> str | None:
            idx = col.get(key)
            if idx is None or idx >= len(row):
                return None
            value = row[idx].strip()
            return value or None

        name = cell("name") or cell("symbol") or "Unknown"
        symbol = cell("symbol")
        quantity = parse_decimal(cell("quantity"))
        current_price = parse_decimal(cell("price"))
        market_value = parse_decimal(cell("value"))
        average_cost = parse_decimal(cell("average_cost"))
        cost_basis = parse_decimal(cell("cost_basis"))
        unrealized_pnl = parse_decimal(cell("pnl"))
        currency = parse_currency_hint(" ".join(row)) or default_currency

        holdings.append(
            NormalizedHolding(
                symbol=symbol,
                security_name=name,
                quantity=quantity,
                current_price=current_price,
                market_value=market_value,
                average_cost=average_cost,
                cost_basis=cost_basis,
                unrealized_pnl=unrealized_pnl,
                currency=currency,
                asset_type="equity",
                valuation_timestamp=ts,
            )
        )
    return holdings


def _column_map(header: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for i, h in enumerate(header):
        if h in {"symbol", "ticker", "code"}:
            mapping["symbol"] = i
        elif h in {"name", "security", "security name", "stock", "instrument"}:
            mapping["name"] = i
        elif h in {"qty", "quantity", "units", "shares"}:
            mapping["quantity"] = i
        elif h in {"price", "current price", "last price", "market price"}:
            mapping["price"] = i
        elif h in {"value", "market value", "mv", "total value"}:
            mapping["value"] = i
        elif h in {"avg cost", "average cost", "avg. cost", "avg price"}:
            mapping["average_cost"] = i
        elif h in {"cost basis", "cost", "total cost"}:
            mapping["cost_basis"] = i
        elif h in {"p/l", "pnl", "unrealized", "unrealized p/l", "gain/loss"}:
            mapping["pnl"] = i
    return mapping


def parse_holdings_html(html: str, *, default_currency: str | None = "NGN") -> list[NormalizedHolding]:
    tables = extract_tables(html)
    for table in tables:
        holdings = holdings_from_table(table, default_currency=default_currency)
        if holdings:
            return holdings
    return []


def default_account() -> NormalizedAccount:
    """Fallback when the portal exposes a single undifferentiated portfolio."""
    return NormalizedAccount(
        external_account_id="default",
        account_name="Afrinvestor Portfolio",
        account_type="brokerage",
        currency="NGN",
    )


def parse_accounts_from_page_text(text: str) -> list[NormalizedAccount]:
    """Best-effort account detection from page text. Returns default if none found."""
    accounts: list[NormalizedAccount] = []
    # Look for patterns like "Portfolio 1", "Main Account"
    for match in re.finditer(
        r"(?:Portfolio|Account)\s*[:#]?\s*([A-Za-z0-9 _-]{2,40})",
        text,
        re.IGNORECASE,
    ):
        name = match.group(0).strip()
        accounts.append(
            NormalizedAccount(
                external_account_id=re.sub(r"\s+", "_", name.lower()),
                account_name=name,
                account_type="brokerage",
                currency="NGN",
            )
        )
    if not accounts:
        accounts.append(default_account())
    # Dedupe by external id
    seen: set[str] = set()
    unique: list[NormalizedAccount] = []
    for acct in accounts:
        if acct.external_account_id not in seen:
            seen.add(acct.external_account_id)
            unique.append(acct)
    return unique


def parse_cash_from_text(text: str) -> Decimal | None:
    patterns = [
        r"(?:wallet|cash|available)\s*(?:balance)?\s*[:.]?\s*[₦N]?[\s]*([\d,]+\.?\d*)",
        r"[₦]\s*([\d,]+\.?\d*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return parse_decimal(match.group(1))
    return None


def extraction_diagnostics(html: str) -> dict[str, Any]:
    tables = extract_tables(html)
    return {
        "table_count": len(tables),
        "row_counts": [len(t) for t in tables],
        "holdings_parsed": len(parse_holdings_html(html)),
    }


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    return parse_decimal(str(value))


def accounts_from_portfolio_list(payload: Any) -> list[NormalizedAccount]:
    """Map /api/all/portfolio/list JSON into accounts."""
    rows = payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []
    accounts: list[NormalizedAccount] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        external_id = str(row.get("id") or row.get("name") or "").strip()
        if not external_id:
            continue
        label = (row.get("label") or row.get("name") or external_id)
        accounts.append(
            NormalizedAccount(
                external_account_id=external_id,
                account_name=str(label),
                account_type=str(row.get("type") or "brokerage"),
                currency="NGN",
            )
        )
    if not accounts:
        accounts.append(default_account())
    return accounts


def holdings_from_equity_api(payload: Any, *, default_currency: str = "NGN") -> list[NormalizedHolding]:
    """Map /api/all/portfolio/assets/{id}/equity/holdings JSON into holdings.

    Observed Afrinvestor shape (2026-10-27):
      { holdings: [{ security_name, security_label, quantity_held, market_price,
                     market_value, cost_basis (avg/unit), total_gain, value_date, ...}],
        stats: {...} }
    """
    rows: list[Any]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for key in ("holdings", "data", "assets", "equities", "items", "result"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
        else:
            rows = [payload] if payload.get("security_name") or payload.get("symbol") else []
    else:
        rows = []

    holdings: list[NormalizedHolding] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # On Afrinvestor, security_name is the ticker (e.g. ACCESSCORP)
        symbol = (
            row.get("symbol")
            or row.get("ticker")
            or row.get("security_name")
            or row.get("security_code")
            or row.get("code")
        )
        name = (
            row.get("security_label")
            or row.get("name")
            or row.get("description")
            or (str(symbol) if symbol else None)
        )
        if not name:
            continue
        quantity = _to_decimal(
            row.get("quantity_held")
            or row.get("quantity")
            or row.get("qty")
            or row.get("units")
        )
        current_price = _to_decimal(
            row.get("market_price")
            or row.get("current_price")
            or row.get("price")
            or row.get("last_price")
        )
        market_value = _to_decimal(
            row.get("market_value")
            or row.get("valuation")
            or row.get("value")
        )
        # Afrinvestor `cost_basis` is average cost per unit
        average_cost = _to_decimal(
            row.get("average_cost")
            or row.get("avg_cost")
            or row.get("cost_basis")
            or row.get("average_price")
        )
        cost_basis = None
        if average_cost is not None and quantity is not None:
            cost_basis = average_cost * quantity
        unrealized_pnl = _to_decimal(
            row.get("total_gain")
            or row.get("unrealized_pnl")
            or row.get("pnl")
            or row.get("gain")
        )
        currency = row.get("currency") or default_currency
        isin = row.get("isin")
        valuation_timestamp = datetime.now(timezone.utc)
        value_date = row.get("value_date")
        if isinstance(value_date, str) and value_date.strip():
            try:
                valuation_timestamp = datetime.fromisoformat(value_date).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        asset_type = str(row.get("security_type") or "equity").lower()
        holdings.append(
            NormalizedHolding(
                symbol=str(symbol) if symbol else None,
                security_name=str(name),
                isin=str(isin) if isin else None,
                asset_type=asset_type,
                quantity=quantity,
                average_cost=average_cost,
                cost_basis=cost_basis,
                current_price=current_price,
                market_value=market_value,
                unrealized_pnl=unrealized_pnl,
                currency=str(currency) if currency else default_currency,
                valuation_timestamp=valuation_timestamp,
            )
        )
    return holdings


def cash_from_wallet_api(payload: Any) -> Decimal | None:
    if not isinstance(payload, dict):
        return None
    return _to_decimal(
        payload.get("total_balance")
        or payload.get("purchasing_balance")
        or payload.get("available_cash")
        or payload.get("balance")
    )


def deposit_instructions_from_dedicated_api(payload: Any) -> DepositInstructions:
    """Map POST /api/all/wallet/dedicated JSON into DepositInstructions.

    Observed shape: ``[{bank_code, bank_name, account_name, account_number}, ...]``
    """
    row: dict[str, Any] | None = None
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            row = first
    elif isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            row = data[0]
        elif isinstance(data, dict):
            row = data
        else:
            row = payload
    if not row:
        raise ValueError("Dedicated wallet response did not include account details")

    account_number = row.get("account_number") or row.get("nuban") or row.get("accountNumber")
    bank_name = row.get("bank_name") or row.get("bankName")
    account_name = row.get("account_name") or row.get("accountName")
    bank_code = row.get("bank_code") or row.get("bankCode")
    if not account_number:
        raise ValueError("Dedicated wallet response missing account_number")

    return DepositInstructions(
        bank_name=str(bank_name) if bank_name else None,
        account_number=str(account_number),
        account_name=str(account_name) if account_name else None,
        currency="NGN",
        provider_reference=str(bank_code) if bank_code else None,
        bank_code=str(bank_code) if bank_code else None,
        raw={k: row.get(k) for k in ("bank_code", "bank_name", "account_name", "account_number") if k in row},
    )


def instruments_from_equities_api(
    payload: Any,
    *,
    market_id: str | None = None,
    page: int = 1,
    per_page: int = 100,
) -> InstrumentListResult:
    """Map GET /api/all/invest/equities JSON into InstrumentListResult."""
    items: list[Any] = []
    total: int | None = None
    if isinstance(payload, list):
        items = payload
        total = len(items)
    elif isinstance(payload, dict):
        for key in ("data", "equities", "items", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                items = val
                break
        total_raw = payload.get("total")
        if total_raw is None and isinstance(payload.get("meta"), dict):
            total_raw = payload["meta"].get("total")
        total = int(total_raw) if total_raw is not None else len(items)

    instruments: list[MarketInstrument] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol") or item.get("secId") or item.get("ticker")
        if not symbol:
            continue
        exchange = item.get("exchangeId") or item.get("exchange_id")
        mid = (
            market_id
            or (str(exchange) if exchange else None)
            or str(item.get("marketId") or "")
            or None
        )
        instruments.append(
            MarketInstrument(
                symbol=str(symbol).upper(),
                name=str(item.get("secDesc") or item.get("description") or symbol),
                market_id=str(mid) if mid else None,
                exchange_id=str(exchange) if exchange else None,
                currency=str(item.get("currency")) if item.get("currency") else None,
                last_price=_to_decimal(item.get("price") or item.get("lastPx")),
                asset_type=str(item.get("marketSegmentId") or item.get("secType") or "EQUITY"),
                provider_instrument_id=str(item.get("id")) if item.get("id") else str(symbol),
            )
        )
    return InstrumentListResult(
        market_id=market_id,
        page=page,
        per_page=per_page,
        total=total,
        instruments=instruments,
    )


def find_equity_in_search(payload: Any, symbol: str) -> dict[str, Any] | None:
    """Find an equity row by symbol / secId in market search results."""
    needle = symbol.strip().upper()
    items: list[Any] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("data", "equities", "items", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                items = val
                break
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("symbol", "secId", "sec_id", "ticker", "security_name"):
            val = item.get(key)
            if val is not None and str(val).strip().upper() == needle:
                return item
    return None


def order_result_from_broker_response(
    payload: Any,
    *,
    side: str,
    symbol: str,
) -> OrderResult:
    data = payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        data = payload["data"]
    if not isinstance(data, dict):
        data = {"raw": payload}

    order_id = (
        data.get("order_id")
        or data.get("orderId")
        or data.get("id")
        or data.get("orderNo")
        or data.get("order_no")
    )
    status = (
        data.get("orderStatus")
        or data.get("order_status")
        or data.get("status")
        or ("submitted" if order_id else "accepted")
    )
    message = data.get("message") or data.get("title") or data.get("routingMessage")
    filled = _to_decimal(data.get("filled_qty") or data.get("filledQty") or data.get("executedQty"))

    return OrderResult(
        external_order_id=str(order_id) if order_id is not None else None,
        status=str(status),
        side=OrderSide(side),
        symbol=symbol,
        filled_qty=filled,
        message=str(message) if message else None,
        raw=data if isinstance(data, dict) else {"value": data},
    )


def summary_from_nets_api(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    return {
        "total_market_value": _to_decimal(payload.get("total_valuation")),
        "cash_value": _to_decimal(payload.get("available_cash")),
        "equity_value": _to_decimal(payload.get("total_equity_valuation")),
        "currency": "NGN",
    }
