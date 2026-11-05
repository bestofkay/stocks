"""Vetiva parser tests (anonymized fixtures, no live credentials)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.connectors.vetiva import parser

FIX = Path(__file__).parent / "fixtures" / "vetiva"


def load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_session_and_profile():
    info = parser.session_from_auth({"message": "ok", "status_code": 200, "data": {
        "is2faenabled": False,
        "session": {"access_token": "a", "refresh_token": "r", "account_id": "acc-1", "expired_at": "x"}}})
    assert info["access_token"] == "a" and info["refresh_token"] == "r" and info["account_id"] == "acc-1"
    assert parser.session_from_auth({"data": {"is2faenabled": True}})["is_2fa_enabled"] is True
    prof = parser.profile_info({"data": {"account_id": "acc-1", "has_trade_account": True, "status": "approved"}})
    assert prof["has_trade_account"] is True and prof["account_id"] == "acc-1"


def test_accounts_and_cash():
    pv = parser.portfolio_value(load("portfolio_value.json"))
    assert pv["trading_balance"] == Decimal("1200.5")
    acct = parser.portfolio_account("acc-1", trading_balance=pv["trading_balance"])
    assert acct.external_account_id == "portfolio:acc-1"
    wallets = parser.wallet_accounts(load("wallets.json"))
    assert [w.external_account_id for w in wallets] == ["wallet:w-1"]
    assert wallets[0].cash_balance == Decimal("250.00")  # available, not ledger
    assert parser.wallet_balance(load("wallets.json"), "w-1") == Decimal("250.00")


def test_holdings_derive_price_and_cost():
    rows, total = parser.assets_page(load("portfolio_assets.json"))
    assert total == 2
    h = parser.holdings_from_assets(rows)
    dang = h[0]
    assert dang.symbol == "DANGCEM" and dang.asset_type == "equity"
    assert dang.quantity == Decimal("10") and dang.market_value == Decimal("14200")
    assert dang.current_price == Decimal("1420")
    assert dang.cost_basis == Decimal("13000") and dang.average_cost == Decimal("1300")
    assert dang.unrealized_pnl == Decimal("1200")
    mmf = h[1]
    assert mmf.symbol is None and mmf.asset_type == "money_market"
    assert mmf.cost_basis is None and mmf.unrealized_pnl is None  # unknown stays None
    assert mmf.resolve_security_identifier() == "vetiva:as-2"
    assert parser.holdings_from_assets(parser.assets_page(load("portfolio_assets_empty.json"))[0]) == []


def test_summary():
    s = parser.summary_from_breakdown(load("portfolio_value.json"), load("breakdown_expanded.json"))
    assert s.total_market_value == Decimal("15000")
    assert s.unrealized_pnl == Decimal("1200") and s.total_cost_basis == Decimal("13800")
    assert s.cash_value == Decimal("1200.5")


def test_transactions_signed():
    txns = parser.transactions_from_records(load("trading_transactions.json"), prefix="trade")
    assert [t.external_transaction_id for t in txns] == ["trade:t-1", "trade:t-2"]
    assert txns[0].transaction_type == "deposit" and txns[0].total_amount == Decimal("15000")
    assert txns[1].transaction_type == "buy_fill" and txns[1].total_amount == Decimal("-13000")
    assert parser.transactions_from_records({"status_code": 200, "data": [], "total_count": 0}, prefix="wallet") == []


def test_instruments_and_units():
    rows, more = parser.securities_page(load("securities.json"))
    assert more is False
    res = parser.instruments_result(rows, search=None, page=1, per_page=10)
    assert [i.symbol for i in res.instruments] == ["ABBEYBANK", "DANGCEM"]  # inactive excluded
    assert res.instruments[1].last_price == Decimal("1420")
    assert [i.symbol for i in parser.instruments_result(rows, search="cement", page=1, per_page=10).instruments] == ["DANGCEM"]
    assert parser.units_for_amount(Decimal("10000"), Decimal("1420")) == 7
    assert parser.last_price_from_stats({"data": {"lastPx": 1420, "refPx": 1400}}) == Decimal("1420")


def test_order_result_and_errors():
    r = parser.order_result({"message": "Order placed", "data": {"order_id": "o-9", "queued": True, "next_open_at": "x"}},
                            side="buy", symbol="DANGCEM")
    assert r.external_order_id == "o-9" and r.status == "queued" and r.message == "Order placed"
    assert parser.broker_message({"error": "You Do Not Have A Trade Account Yet", "reason": "rpc"}) == "You Do Not Have A Trade Account Yet"
