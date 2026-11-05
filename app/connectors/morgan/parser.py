"""Parse Morgan Capital modern-app page text / payloads into normalized models.

Live pages are RSC/HTML (no public REST). Parsers operate on ``document.body``
innerText captured after hydration.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
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


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip()
    if not text or text in {"-", "—", "N/A", "n/a", "null"}:
        return None
    text = re.sub(r"[₦$£€,\s]", "", text)
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    # trailing percent junk
    text = text.rstrip("%")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def account_from_portal_text(text: str) -> NormalizedAccount:
    """Build the single brokerage account from dashboard/portfolio chrome."""
    name = "Morgan Capital"
    # "SO\nSoji Oriyomi Okunuga\nCSCS pending"
    m = re.search(r"\n([A-Z][^\n]{2,80})\nCSCS", text)
    if m:
        name = m.group(1).strip()
    else:
        m = re.search(r"Good (?:morning|afternoon|evening), ([A-Za-z]+)", text)
        if m:
            name = f"Morgan Capital ({m.group(1)})"
    cash = cash_from_wallet_text(text)
    return NormalizedAccount(
        external_account_id="morgan-primary",
        account_name=name,
        account_type="brokerage",
        currency="NGN",
        cash_balance=cash,
    )


def cash_from_wallet_text(text: str) -> Decimal | None:
    m = re.search(r"AVAILABLE BALANCE\s*\n\s*(₦[\d,]+(?:\.\d+)?)", text, re.I)
    if m:
        return _to_decimal(m.group(1))
    m = re.search(r"WALLET\s*[·•].*?\n.*?INSTANT\s*\n\s*(₦[\d,]+(?:\.\d+)?)", text, re.I | re.S)
    if m:
        return _to_decimal(m.group(1))
    return None


def holdings_from_portfolio_text(text: str, *, default_currency: str = "NGN") -> list[NormalizedHolding]:
    if re.search(r"No holdings yet|0 positions|Your Nigerian portfolio starts here", text, re.I):
        return []
    holdings: list[NormalizedHolding] = []
    # Rows like: SEPLAT\nOil & Gas\n₦5,890.00 ... with units — keep conservative
    for m in re.finditer(
        r"\b([A-Z]{3,12})\b\n([^\n]{0,40})\n(?:.*?)\n?(\d+(?:\.\d+)?)\n(₦[\d,]+(?:\.\d+)?)",
        text,
    ):
        symbol, name, qty, price = m.group(1), m.group(2).strip(), m.group(3), m.group(4)
        if symbol in {"NGX", "IPO", "CSCS", "NDPR", "SEC", "USD", "NGN", "ALL", "OPEN"}:
            continue
        q = _to_decimal(qty)
        p = _to_decimal(price)
        holdings.append(
            NormalizedHolding(
                symbol=symbol,
                security_name=name or symbol,
                asset_type="equity",
                quantity=q,
                current_price=p,
                market_value=(q * p) if q is not None and p is not None else None,
                currency=default_currency,
            )
        )
    return holdings


def deposit_instructions_from_wallet_text(text: str) -> DepositInstructions:
    bank = None
    m_bank = re.search(
        r"(Wema(?:\s+Bank)?|GTBank|Access Bank|Zenith Bank|Moniepoint|Providus)",
        text,
        re.I,
    )
    if m_bank:
        bank = m_bank.group(1)
    elif re.search(r"Powered by Wema|WALLET\s*[·•]\s*WEMA", text, re.I):
        bank = "Wema Bank"

    account_number: str | None = None
    # Observed: "YOUR VIRTUAL ACCOUNT\n9995 911 821\nWema Bank · MorganCapital/Name"
    m_spaced = re.search(
        r"YOUR VIRTUAL ACCOUNT\s*\n\s*([0-9][0-9\s]{8,14}[0-9])\s*\n",
        text,
        re.I,
    )
    if m_spaced:
        digits = re.sub(r"\D", "", m_spaced.group(1))
        if len(digits) == 10:
            account_number = digits
    if not account_number:
        m_acct = re.search(
            r"(?:Account(?:\s+number)?|Acct|NUBAN|Virtual account|VA)[^\d]{0,60}(\d{10})",
            text,
            re.I,
        )
        if m_acct:
            account_number = m_acct.group(1)
    if not account_number:
        m_acct = re.search(r"(?:Wema|funding|transfer)[^\d]{0,80}(\d{10})", text, re.I)
        if m_acct:
            account_number = m_acct.group(1)
    if not account_number:
        # Spaced NUBAN anywhere: 9995 911 821
        for m in re.finditer(r"\b(\d{3,4}\s+\d{3}\s+\d{3,4})\b", text):
            digits = re.sub(r"\D", "", m.group(1))
            if len(digits) == 10 and not digits.startswith("234"):
                account_number = digits
                break
    if not account_number:
        for m in re.finditer(r"\b(\d{10})\b", text):
            if not m.group(1).startswith("234"):
                account_number = m.group(1)
                break

    if not account_number:
        if re.search(
            r"issued the moment your KYC is approved|Finish KYC|Complete your KYC",
            text,
            re.I,
        ):
            raise ValueError(
                "Morgan virtual account is not issued until KYC is approved "
                "(observed on wallet page)."
            )
        raise ValueError("Morgan wallet page has no virtual-account number yet")

    acct_name = None
    m_name = re.search(r"MorganCapital/([^\n]+)", text)
    if m_name:
        acct_name = m_name.group(1).strip()
    else:
        m_name = re.search(r"\n([A-Z][^\n]{2,80})\nCSCS", text)
        if m_name:
            acct_name = m_name.group(1).strip()
    return DepositInstructions(
        bank_name=bank,
        account_number=account_number,
        account_name=acct_name,
        currency="NGN",
        raw={"source": "wallet_page_text", "bank": bank},
    )


def instruments_from_markets_text(
    text: str,
    *,
    market_id: str = "NGX",
    page: int = 1,
    per_page: int = 100,
    search: str | None = None,
) -> InstrumentListResult:
    instruments: list[MarketInstrument] = []
    seen: set[str] = set()
    # Observed card layout: optional 2-letter tile\nSYMBOL\nSector\n₦price\n±pct%
    for m in re.finditer(
        r"(?:^[A-Z]{1,3}\n)?([A-Z]{3,12})\n([A-Za-z][^\n]{0,60})\n(₦[\d,]+(?:\.\d+)?)\n([+\u2212\-]?[\d.]+%)",
        text,
        re.M,
    ):
        symbol = m.group(1)
        if symbol in seen or symbol in {"NGX", "USD", "NGN", "IPO", "SEC", "CSCS", "ALL", "OPEN"}:
            continue
        seen.add(symbol)
        instruments.append(
            MarketInstrument(
                symbol=symbol,
                name=m.group(2).strip(),
                market_id=market_id,
                exchange_id=market_id,
                currency="NGN",
                last_price=_to_decimal(m.group(3)),
                asset_type="EQUITY",
                provider_instrument_id=symbol,
            )
        )
    # Fallback: SYMBOL then price on nearby lines (table rows with Trade)
    if not instruments:
        for m in re.finditer(
            r"\b([A-Z]{4,12})\b(?:\n[^\n]+){0,3}\n(₦[\d,]+(?:\.\d+)?)",
            text,
        ):
            symbol = m.group(1)
            if symbol in seen or symbol in {"NGX", "BANKING", "SHARE"}:
                continue
            seen.add(symbol)
            instruments.append(
                MarketInstrument(
                    symbol=symbol,
                    name=symbol,
                    market_id=market_id,
                    exchange_id=market_id,
                    currency="NGN",
                    last_price=_to_decimal(m.group(2)),
                    asset_type="EQUITY",
                    provider_instrument_id=symbol,
                )
            )
    if search:
        q = search.strip().upper()
        instruments = [
            i for i in instruments if q in i.symbol.upper() or (i.name and q in i.name.upper())
        ]
    total = len(instruments)
    start = max(0, (page - 1) * per_page)
    end = start + per_page
    return InstrumentListResult(
        market_id=market_id,
        page=page,
        per_page=per_page,
        total=total,
        instruments=instruments[start:end],
    )


def order_result_from_payload(payload: Any, *, side: str, symbol: str) -> OrderResult:
    data = payload if isinstance(payload, dict) else {"raw": payload}
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        data = payload["data"]
    order_id = data.get("order_id") or data.get("id") or data.get("reference")
    status = data.get("status") or ("submitted" if order_id else "accepted")
    return OrderResult(
        external_order_id=str(order_id) if order_id is not None else None,
        status=str(status),
        side=OrderSide(side),
        symbol=symbol,
        filled_qty=_to_decimal(data.get("filled_qty") or data.get("quantity")),
        message=str(data.get("message") or "") or None,
        raw=data if isinstance(data, dict) else {"value": data},
    )


# Keep helpers used by unit tests / optional JSON payloads
def accounts_from_payload(payload: Any) -> list[NormalizedAccount]:
    if isinstance(payload, dict) and payload.get("id"):
        return [
            NormalizedAccount(
                external_account_id=str(payload["id"]),
                account_name=str(payload.get("name") or payload["id"]),
                account_type="brokerage",
                currency=str(payload.get("currency") or "NGN"),
                cash_balance=_to_decimal(payload.get("wallet_balance")),
            )
        ]
    items = []
    if isinstance(payload, dict):
        for key in ("accounts", "portfolios", "data", "items"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
    elif isinstance(payload, list):
        items = payload
    out: list[NormalizedAccount] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ext = item.get("id") or item.get("account_id")
        if not ext:
            continue
        out.append(
            NormalizedAccount(
                external_account_id=str(ext),
                account_name=str(item.get("name") or ext),
                account_type=str(item.get("type") or "brokerage"),
                currency=str(item.get("currency") or "NGN"),
                cash_balance=_to_decimal(item.get("wallet_balance") or item.get("cash_balance")),
            )
        )
    return out


def holdings_from_payload(payload: Any, *, default_currency: str = "NGN") -> list[NormalizedHolding]:
    if isinstance(payload, str):
        return holdings_from_portfolio_text(payload, default_currency=default_currency)
    items: list[Any] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("holdings", "positions", "data", "items"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
    out: list[NormalizedHolding] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol") or item.get("ticker")
        name = item.get("name") or symbol
        if not name:
            continue
        qty = _to_decimal(item.get("quantity") or item.get("units"))
        price = _to_decimal(item.get("price") or item.get("last_price"))
        value = _to_decimal(item.get("market_value") or item.get("value"))
        if value is None and qty is not None and price is not None:
            value = qty * price
        out.append(
            NormalizedHolding(
                symbol=str(symbol).upper() if symbol else None,
                security_name=str(name),
                asset_type=str(item.get("asset_type") or "equity"),
                quantity=qty,
                current_price=price,
                market_value=value,
                currency=str(item.get("currency") or default_currency),
            )
        )
    return out


def cash_from_wallet_payload(payload: Any) -> Decimal | None:
    if isinstance(payload, str):
        return cash_from_wallet_text(payload)
    if isinstance(payload, (int, float, str, Decimal)):
        return _to_decimal(payload)
    if not isinstance(payload, dict):
        return None
    return _to_decimal(
        payload.get("available_balance")
        or payload.get("wallet_balance")
        or payload.get("balance")
        or payload.get("cash")
    )


def deposit_instructions_from_payload(payload: Any) -> DepositInstructions:
    if isinstance(payload, str):
        return deposit_instructions_from_wallet_text(payload)
    row = payload if isinstance(payload, dict) else None
    if isinstance(payload, dict):
        data = payload.get("data") or payload.get("virtual_account")
        if isinstance(data, dict):
            row = data
    if not row:
        raise ValueError("Morgan deposit payload missing")
    account_number = row.get("account_number") or row.get("nuban")
    if not account_number:
        raise ValueError("Morgan deposit payload missing account_number")
    return DepositInstructions(
        bank_name=str(row.get("bank_name") or row.get("bank") or "") or None,
        account_number=str(account_number),
        account_name=str(row.get("account_name") or "") or None,
        currency=str(row.get("currency") or "NGN"),
        bank_code=str(row.get("bank_code") or "") or None,
        raw={k: row.get(k) for k in row if k in {"bank_name", "bank", "account_number", "account_name"}},
    )


def instruments_from_payload(
    payload: Any,
    *,
    market_id: str | None = "NGX",
    page: int = 1,
    per_page: int = 100,
) -> InstrumentListResult:
    if isinstance(payload, str):
        return instruments_from_markets_text(
            payload, market_id=market_id or "NGX", page=page, per_page=per_page
        )
    items: list[Any] = []
    total: int | None = None
    if isinstance(payload, list):
        items = payload
        total = len(items)
    elif isinstance(payload, dict):
        for key in ("data", "instruments", "items", "results"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
        total = int(payload["total"]) if payload.get("total") is not None else len(items)
    instruments: list[MarketInstrument] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol") or item.get("ticker")
        if not symbol:
            continue
        instruments.append(
            MarketInstrument(
                symbol=str(symbol).upper(),
                name=str(item.get("name") or symbol),
                market_id=str(item.get("market_id") or market_id or ""),
                exchange_id=str(item.get("exchangeId") or market_id or "") or None,
                currency=str(item.get("currency") or "NGN"),
                last_price=_to_decimal(item.get("price") or item.get("last_price")),
                asset_type=str(item.get("asset_type") or "EQUITY"),
                provider_instrument_id=str(item.get("id") or symbol),
            )
        )
    return InstrumentListResult(
        market_id=market_id,
        page=page,
        per_page=per_page,
        total=total,
        instruments=instruments,
    )


def parse_itrade_auth_body(text: str) -> tuple[bool, str]:
    """Legacy helper kept for unit tests; modern app does not use iTrade."""
    raw = (text or "").strip()
    if not raw:
        return False, "Empty auth response"
    if raw.lower().startswith("errmsg"):
        parts = raw.split("||", 1)
        return False, parts[1].strip() if len(parts) > 1 else raw
    if raw.upper().startswith("OK"):
        return True, "Authenticated"
    return True, raw[:200]
