"""Pure parsers: Atlas Folio WebSocket / settings payloads -> normalized models."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.connectors.models import (
    DepositInstructions,
    InstrumentListResult,
    MarketInfo,
    MarketInstrument,
    NormalizedAccount,
    NormalizedHolding,
    PortfolioSummary,
)


def dec(v: Any) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    text = str(v).strip().replace(",", "").replace("₦", "")
    if not text or text.lower() in {"-", "n/a", "null", "none"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_settings_from_wa_html(html: str) -> dict[str, Any]:
    """Extract the inline ``settings = {...}`` bootstrap from /customers/wa/."""
    out: dict[str, Any] = {}
    m = re.search(r"settings\s*=\s*\{", html)
    if not m:
        return out
    # brace match
    i = m.end() - 1
    depth = 0
    end = None
    for j in range(i, len(html)):
        ch = html[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = j + 1
                break
    if end is None:
        return out
    block = html[m.start() : end]

    def grab_str(key: str) -> str | None:
        mm = re.search(rf"{re.escape(key)}\s*:\s*\"([^\"]*)\"", block)
        return mm.group(1) if mm else None

    def grab_num(key: str) -> int | None:
        mm = re.search(rf"{re.escape(key)}\s*:\s*(\d+)", block)
        return int(mm.group(1)) if mm else None

    def grab_bool(key: str) -> bool | None:
        mm = re.search(rf"{re.escape(key)}\s*:\s*(true|false)", block)
        return None if not mm else mm.group(1) == "true"

    out["id"] = grab_num("id")
    out["user"] = grab_str("user")
    out["email"] = grab_str("email")
    out["nonce"] = grab_str("nonce")
    out["contextPath"] = grab_str("contextPath") or ""
    out["appName"] = grab_str("appName")
    out["appCoy"] = grab_str("appCoy")
    out["canPayOnline"] = grab_bool("canPayOnline")
    out["canUseApp"] = grab_bool("canUseApp")
    out["isTrader"] = grab_bool("isTrader")
    mm = re.search(r"tradeAccounts\s*:\s*\[([^\]]*)\]", block)
    out["tradeAccounts"] = [int(x) for x in re.findall(r"\d+", mm.group(1))] if mm else []
    return out


def accounts_from_settings(settings: dict[str, Any]) -> list[NormalizedAccount]:
    name = str(settings.get("user") or "Atlas account").strip() or "Atlas account"
    out: list[NormalizedAccount] = []
    for acct in settings.get("tradeAccounts") or []:
        out.append(
            NormalizedAccount(
                external_account_id=str(acct),
                account_name=name,
                account_type="brokerage",
                currency="NGN",
            )
        )
    if not out and settings.get("id") is not None:
        out.append(
            NormalizedAccount(
                external_account_id=str(settings["id"]),
                account_name=name,
                account_type="brokerage",
                currency="NGN",
            )
        )
    return out


def cash_from_dashboard(resp: Any) -> Decimal | None:
    if not isinstance(resp, dict):
        return None
    acct = resp.get("acct") if isinstance(resp.get("acct"), dict) else resp
    if not isinstance(acct, dict):
        return None
    return dec(acct.get("avail_bal") if acct.get("avail_bal") is not None else acct.get("bal"))


def summary_from_dashboard(resp: Any, *, cash: Decimal | None = None) -> PortfolioSummary | None:
    if not isinstance(resp, dict):
        return None
    pf = resp.get("pf") if isinstance(resp.get("pf"), dict) else None
    mv = dec(pf.get("t")) if pf else None
    if cash is None:
        cash = cash_from_dashboard(resp)
    if mv is None and cash is None:
        return None
    return PortfolioSummary(
        total_market_value=mv,
        cash_value=cash,
        currency="NGN",
        valuation_source="broker_supplied",
    )


def holdings_from_portfolio_rows(rows: Any) -> list[NormalizedHolding]:
    if not isinstance(rows, list):
        return []
    out: list[NormalizedHolding] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("smbl") or row.get("id") or "").strip().upper()
        if not symbol:
            continue
        qty = dec(row.get("qty") if row.get("qty") is not None else row.get("qtytn"))
        out.append(
            NormalizedHolding(
                symbol=symbol,
                security_name=str(row.get("desc") or symbol),
                asset_type="equity",
                quantity=qty,
                average_cost=dec(row.get("costPx") or row.get("bePx")),
                cost_basis=dec(row.get("assetCost")),
                current_price=dec(row.get("refPx")),
                market_value=dec(row.get("mktVal") or row.get("netAssetVal")),
                unrealized_pnl=dec(row.get("gainLoss")),
                currency="NGN",
            )
        )
    return out


def holdings_from_pf_summary(resp: Any) -> list[NormalizedHolding]:
    """Fallback when per-symbol grid is empty: one synthetic row per asset class."""
    if not isinstance(resp, dict):
        return []
    pf = resp.get("pf") if isinstance(resp.get("pf"), dict) else resp
    if not isinstance(pf, dict):
        return []
    rows = pf.get("d") or []
    out: list[NormalizedHolding] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "Equity").strip() or "Equity"
        out.append(
            NormalizedHolding(
                symbol=label.upper().replace(" ", "_"),
                security_name=label,
                asset_type="equity",
                quantity=dec(row.get("qty")),
                market_value=dec(row.get("value")),
                currency="NGN",
            )
        )
    return out


def markets_from_exchange_select(resp: Any) -> list[MarketInfo]:
    if not isinstance(resp, list):
        return [MarketInfo(market_id="NGX", name="Nigerian Exchange (NGX)", currency="NGN")]
    out: list[MarketInfo] = []
    for row in resp:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        value = str(row.get("value") or "").strip()
        if not label:
            continue
        out.append(MarketInfo(market_id=label or value, name=label or value, currency="NGN"))
    return out or [MarketInfo(market_id="NGX", name="Nigerian Exchange (NGX)", currency="NGN")]


def instruments_from_mde(
    rows: Any,
    *,
    market_id: str = "NGX",
    search: str | None = None,
    page: int = 1,
    per_page: int = 100,
) -> InstrumentListResult:
    items: list[MarketInstrument] = []
    needle = (search or "").strip().upper()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("id") or "").strip().upper()
        if not symbol:
            continue
        if needle and needle not in symbol:
            continue
        items.append(
            MarketInstrument(
                symbol=symbol,
                name=symbol,
                market_id=market_id,
                exchange_id=str(row.get("exchng") or market_id),
                currency="NGN",
                last_price=dec(row.get("rlstPx") or row.get("lstPx") or row.get("rPx")),
                asset_type="equity",
                provider_instrument_id=symbol,
            )
        )
    total = len(items)
    page = max(1, page)
    per_page = max(1, min(per_page, 500))
    start = (page - 1) * per_page
    return InstrumentListResult(
        market_id=market_id,
        instruments=items[start : start + per_page],
        total=total,
        page=page,
        per_page=per_page,
    )


def deposit_instructions_from_settings(settings: dict[str, Any]) -> DepositInstructions:
    can_pay = bool(settings.get("canPayOnline"))
    accts = settings.get("tradeAccounts") or []
    ref = str(accts[0]) if accts else str(settings.get("id") or "")
    return DepositInstructions(
        bank_name="Atlass Portfolios (manual funding)" if not can_pay else "WebPay (portal)",
        account_number=ref or None,
        account_name=str(settings.get("user") or "") or None,
        currency="NGN",
        provider_reference=ref or None,
        raw={
            "funding_method": "portal_webpay" if can_pay else "contact_broker",
            "can_pay_online": can_pay,
            "contact_email": settings.get("email") or "info@atlassportfolios.com",
            "note": (
                "Fund via the CitiTrader WebPay flow"
                if can_pay
                else "Online card funding is disabled on this portal; fund through Atlass Portfolios using your trading account reference."
            ),
        },
    )
