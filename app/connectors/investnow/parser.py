"""Pure parsers for JSON the InvestNow app loads (api.investnow.ng).

Envelope: ``{response_code, message, data}``. Portfolio groups (assetManagement,
trustees, wealthManagement) hold ``[{customerInformation, portfolios: [{cashAccount,
fundAccount, placement}]}]``; field names below are the app's own (odd casing included).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.connectors.models import NormalizedAccount, NormalizedHolding, NormalizedTransaction, PortfolioSummary

GROUPS = {"assetManagement": "mutual_fund", "trustees": "trust", "wealthManagement": "wealth_management"}


def dec(v: Any) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    t = str(v).strip().replace(",", "").replace("₦", "")
    if not t:
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def data(payload: Any) -> Any:
    return payload.get("data") if isinstance(payload, dict) and "data" in payload else payload


def _first(*vals):
    for v in vals:
        if v is not None:
            return v
    return None


def portfolio_entries(payload: Any) -> list[dict[str, Any]]:
    """Flatten groups -> entries ``{group, kind, cashAccount, fundAccount, placement}``."""
    d = data(payload)
    info = d.get("uceeCustomerPortfolioInfo") if isinstance(d, dict) else None
    if not isinstance(info, dict):
        return []
    out = []
    for group, kind in GROUPS.items():
        for block in info.get(group) or []:
            if not isinstance(block, dict):
                continue
            for p in block.get("portfolios") or []:
                if isinstance(p, dict):
                    out.append({"group": group, "kind": kind, **p})
    return out


def securities_present(payload: Any) -> bool:
    d = data(payload)
    info = d.get("uceeCustomerPortfolioInfo") if isinstance(d, dict) else None
    return bool(isinstance(info, dict) and info.get("securities"))


def _ids(e: dict[str, Any]) -> tuple[str | None, str]:
    ca = e.get("cashAccount") or {}
    pl = e.get("placement") or {}
    acct = _first(ca.get("account_NO"), pl.get("dda_ACCOUNT_ID"), pl.get("placement_ID"))
    name = _first(pl.get("product_DESC"), ca.get("account_NAME"), e["kind"].replace("_", " ").title())
    return (str(acct) if acct is not None else None), str(name)


def accounts(entries: list[dict[str, Any]]) -> list[NormalizedAccount]:
    out, seen = [], set()
    for e in entries:
        acct, name = _ids(e)
        if not acct or acct in seen:
            continue
        seen.add(acct)
        ca = e.get("cashAccount") or {}
        out.append(NormalizedAccount(
            external_account_id=acct, account_name=f"{name} ({acct})", account_type=e["kind"].upper(),
            currency=ca.get("currency_ID") or "NGN", cash_balance=dec(ca.get("current_BALANCE")),
        ))
    return out


def holdings_for(entries: list[dict[str, Any]], account_id: str) -> list[NormalizedHolding]:
    out = []
    for e in entries:
        acct, name = _ids(e)
        if acct != account_id:
            continue
        fa, pl, ca = e.get("fundAccount") or {}, e.get("placement") or {}, e.get("cashAccount") or {}
        value = _first(dec(fa.get("currenT_VALUE")), dec(pl.get("current_BALANCE_AMOUNT")))
        cost = _first(dec(fa.get("totaL_PURCHASE_AMOUNT")), dec(pl.get("investment_AMOUNT")))
        accrued = _first(dec(fa.get("outstandinG_ACCRUED_INTEREST")), dec(pl.get("accrued_INTEREST_AMOUNT")))
        if value is None and cost is None:
            continue
        code = _first(pl.get("product_Code"), ca.get("product_ID"))
        out.append(NormalizedHolding(
            symbol=str(code) if code else None, security_name=name, asset_type=e["kind"],
            cost_basis=cost, market_value=value,
            unrealized_pnl=(value - cost) if value is not None and cost is not None else accrued,
            currency=ca.get("currency_ID") or "NGN",
            security_identifier=f"investnow:{e['kind']}:{code or acct}",
        ))
    return out


def summary_for(entries: list[dict[str, Any]], account_id: str) -> PortfolioSummary | None:
    h = holdings_for(entries, account_id)
    acct = next((a for a in accounts(entries) if a.external_account_id == account_id), None)
    if acct is None:
        return None
    mv = sum((x.market_value or Decimal("0") for x in h), Decimal("0"))
    costs = [x.cost_basis for x in h]
    cost = sum(costs, Decimal("0")) if all(c is not None for c in costs) else None
    return PortfolioSummary(total_market_value=mv, total_cost_basis=cost,
                            unrealized_pnl=(mv - cost) if cost is not None else None,
                            cash_value=acct.cash_balance, currency=acct.currency or "NGN")


def _dt(v: Any) -> datetime | None:
    if not v or not isinstance(v, str):
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def transactions(invoices: Any, withdrawals: Any = None) -> list[NormalizedTransaction]:
    out = []
    for kind, payload, sign in (("deposit", invoices, 1), ("withdrawal", withdrawals, -1)):
        rows = data(payload)
        if isinstance(rows, dict):
            rows = rows.get("items") or rows.get("list") or rows.get("data") or []
        for i, r in enumerate(rows or []):
            if not isinstance(r, dict):
                continue
            amt = dec(_first(r.get("amount"), r.get("Amount"), r.get("invoiceAmount")))
            ref = _first(r.get("id"), r.get("reference"), r.get("invoiceNo"), r.get("transactionRef"), f"{kind}-{i}")
            out.append(NormalizedTransaction(
                external_transaction_id=f"{kind}:{ref}", transaction_type=str(_first(r.get("status") and kind, kind)),
                total_amount=(sign * abs(amt)) if amt is not None else None,
                currency=_first(r.get("currency"), r.get("currency_ID")) or "NGN",
                transaction_date=_dt(_first(r.get("createdAt"), r.get("dateCreated"), r.get("transactionDate"), r.get("date"))),
            ))
    return out
