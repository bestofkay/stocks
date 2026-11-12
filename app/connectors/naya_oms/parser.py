"""Pure parsers: NaYa OMS ASMX payloads -> normalized models."""

from __future__ import annotations

import re
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


def dec(v: Any) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    text = str(v).strip()
    if not text or text.lower() in {"-", "—", "n/a", "null", "none"}:
        return None
    # strip HTML currency / formatting: &#8358;1,234.56 or ₦1,234.56
    text = re.sub(r"&#\d+;", "", text)
    text = text.replace("₦", "").replace(",", "").replace("%", "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def unwrap_d(payload: Any) -> Any:
    if isinstance(payload, dict) and "d" in payload:
        return payload["d"]
    return payload


def broker_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    d = unwrap_d(payload)
    if isinstance(d, dict):
        for key in ("Message", "message", "Error", "error", "detail"):
            val = d.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    for key in ("Message", "message", "detail"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def parse_dotnet_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    m = re.search(r"/Date\((-?\d+)(?:[+-]\d+)?\)/", text)
    if m:
        try:
            ms = int(m.group(1))
            return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        if len(text) == 10:
            return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


# --- accounts ---------------------------------------------------------------


def cscs_accounts(payload: Any) -> list[dict[str, Any]]:
    data = unwrap_d(payload)
    if not isinstance(data, dict):
        return []
    rows = data.get("CSCSAccounts") or []
    return [r for r in rows if isinstance(r, dict)]


def account_from_cscs(row: dict[str, Any]) -> tuple[NormalizedAccount, dict[str, Any]]:
    cscs = str(row.get("CSCSNumber") or row.get("AccNum") or "").strip()
    name = (row.get("FullName") or f"{row.get('FirstName') or ''} {row.get('LastName') or ''}").strip()
    acct = NormalizedAccount(
        external_account_id=cscs,
        account_name=name or f"CSCS {cscs}",
        account_type="brokerage",
        currency="NGN",
    )
    meta = {
        "client_id": row.get("ClientId"),
        "acc_num": row.get("AccNum"),
        "cscs_number": cscs,
        "can_trade": bool(row.get("CanTrade", True)),
        "email": row.get("Email"),
        "kyc_issue": row.get("KYCIssue") or "",
        "row": row,
    }
    return acct, meta


# --- holdings / cash / summary ----------------------------------------------


def stock_position_payload(payload: Any) -> dict[str, Any]:
    data = unwrap_d(payload)
    return data if isinstance(data, dict) else {}


def holdings_from_positions(payload: Any, *, default_currency: str = "NGN") -> list[NormalizedHolding]:
    data = stock_position_payload(payload)
    items = data.get("StockPositionItems") or []
    out: list[NormalizedHolding] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("Security") or item.get("SecurityID") or "").strip().upper()
        if not symbol:
            continue
        qty = dec(item.get("Quantity"))
        if qty is None or qty == 0:
            continue
        avg = dec(item.get("BEP") or item.get("WAP") or item.get("AverageCost"))
        mv = dec(item.get("MarketValue") or item.get("NetAssetValue"))
        cost = dec(item.get("ActualValue") or item.get("CostValue"))
        px = dec(item.get("ClosingPrice") or item.get("ESP") or item.get("LastPrice"))
        pnl = dec(item.get("Gain") or item.get("GainOrLoss") or item.get("Income"))
        # GainHtml sometimes only available — skip if non-numeric
        if pnl is None:
            pnl = dec(re.sub(r"<[^>]+>", "", str(item.get("GainHtml") or "")))
        out.append(
            NormalizedHolding(
                symbol=symbol,
                security_name=str(item.get("SecurityDescription") or item.get("Description") or symbol),
                asset_type=str(item.get("AssetClass") or item.get("Market") or "equity").lower(),
                quantity=qty,
                average_cost=avg,
                cost_basis=cost,
                current_price=px,
                market_value=mv,
                unrealized_pnl=pnl,
                currency=default_currency,
            )
        )
    return out


def cash_from_positions(payload: Any) -> Decimal | None:
    data = stock_position_payload(payload)
    for key in ("CashBalance", "Cash", "Networth"):
        val = dec(data.get(key))
        if val is not None:
            return val
    # HTML variants
    html = data.get("CashBalanceHtml")
    if html:
        return dec(re.sub(r"<[^>]+>", "", str(html)))
    return None


def summary_from_positions(payload: Any, *, cash: Decimal | None) -> PortfolioSummary:
    data = stock_position_payload(payload)
    mv = dec(data.get("MarketValue")) or dec(data.get("PortfolioValue"))
    if cash is None:
        cash = cash_from_positions(payload)
    return PortfolioSummary(
        total_market_value=mv,
        cash_value=cash,
        currency="NGN",
    )


# --- transactions -----------------------------------------------------------


def transactions_from_statements(payload: Any) -> list[NormalizedTransaction]:
    data = unwrap_d(payload)
    if not isinstance(data, dict):
        return []
    items = data.get("AccountStatementItems") or []
    out: list[NormalizedTransaction] = []
    for i, row in enumerate(items):
        if not isinstance(row, dict):
            continue
        credit = dec(row.get("Credit")) or Decimal("0")
        debit = dec(row.get("Debit")) or Decimal("0")
        if credit == 0 and debit == 0:
            continue
        amount = credit if credit > 0 else -abs(debit)
        desc = str(row.get("Description") or row.get("Narration") or "ledger").strip()
        ext = row.get("Id") or row.get("Reference") or row.get("TranId") or f"{desc}:{row.get('PostedTime') or i}"
        out.append(
            NormalizedTransaction(
                external_transaction_id=str(ext),
                transaction_type="credit" if amount >= 0 else "debit",
                total_amount=amount,
                currency="NGN",
                transaction_date=parse_dotnet_date(
                    row.get("PostedTime") or row.get("PostDate") or row.get("Date") or row.get("TranDate")
                ),
            )
        )
    return out


def cash_from_statements(payload: Any) -> Decimal | None:
    data = unwrap_d(payload)
    if not isinstance(data, dict):
        return None
    for key in ("AvailableBalance", "BeginningBalance"):
        val = dec(data.get(key))
        if val is not None:
            return val
    return None


# --- deposits ---------------------------------------------------------------


def deposit_instructions_from_attributes(
    payload: Any,
    *,
    funding_url: str,
    brand: str = "NaYa OMS",
) -> DepositInstructions:
    """NaYa portals typically fund via Paystack card; attributes carry CSCS identity."""
    data = unwrap_d(payload)
    if not isinstance(data, dict):
        raise ValueError("unexpected account attributes payload")
    cscs = str(data.get("AccountNumber") or "").strip()
    name = str(data.get("AccountName") or "").strip() or None
    if not cscs:
        raise LookupError("no CSCS account number on profile")
    return DepositInstructions(
        bank_name=f"Paystack (card funding via {brand} portal)",
        account_number=cscs,
        account_name=name,
        currency="NGN",
        provider_reference=str(data.get("PortfolioID") or "") or None,
        raw={
            "funding_method": "paystack_card",
            "funding_url": funding_url,
            "note": "Transfer-to-NUBAN is not offered; fund the trading account via card on Deposit.aspx.",
            "linked_bank": data.get("Bank"),
            "linked_bank_account_name": data.get("BankAccountName"),
            # linked bank account number is the client's withdrawal bank — do not present as funding VA
        },
    )


# --- instruments ------------------------------------------------------------


def securities_rows(payload: Any) -> list[dict[str, Any]]:
    data = unwrap_d(payload)
    if not isinstance(data, dict):
        return []
    rows = data.get("SecurityItems") or []
    return [r for r in rows if isinstance(r, dict)]


def instrument_from_security(row: dict[str, Any]) -> MarketInstrument | None:
    symbol = str(row.get("SecurityID") or row.get("Symbol") or "").strip().upper()
    if not symbol:
        return None
    # Prefer ticker id (GTCO) over long name
    name = row.get("Symbol") if row.get("SecurityID") else row.get("SecurityDescription")
    if row.get("SecurityID") and row.get("Symbol"):
        name = row.get("Symbol")
    return MarketInstrument(
        symbol=symbol,
        name=str(name) if name else symbol,
        market_id="NGX",
        exchange_id="NGX",
        currency="NGN",
        last_price=dec(
            row.get("LastTradedPrice")
            or row.get("ClosingPrice")
            or row.get("OpeningPrice")
            or row.get("WeightedAveragePrice")
        ),
        asset_type="equity",
        provider_instrument_id=row.get("SecurityID") or symbol,
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
    mid = (market_id or "NGX").strip().upper()
    filtered: list[MarketInstrument] = []
    for row in rows:
        status = row.get("SecurityTradingStatusString") or ""
        # keep tradeable + others; filter only if explicitly closed when present
        if status and status.lower() in {"closed", "halted", "suspended"}:
            continue
        blob = f"{row.get('SecurityID') or ''} {row.get('Symbol') or ''} {row.get('SecurityDescription') or ''}".lower()
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


def order_result_from_mandate(
    *,
    side: str,
    symbol: str,
    success: bool,
    message: str | None,
    order_id: str | None = None,
) -> OrderResult:
    try:
        side_enum = OrderSide(side.lower())
    except ValueError:
        side_enum = None
    return OrderResult(
        external_order_id=order_id,
        status="submitted" if success else "rejected",
        side=side_enum,
        symbol=symbol,
        message=message,
        raw=None,
    )
