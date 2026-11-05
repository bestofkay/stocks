"""CardinalStone parser tests (anonymized fixtures, no live credentials)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.connectors.cardinalstone import parser

FIX = Path(__file__).parent / "fixtures" / "cardinalstone"


def load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_login_tokens_and_profile_ids():
    info = parser.login_tokens(
        {"token": "t", "refresh_token": "r", "user": {"CustID": 62, "SEC_ID": "c-1", "is2FAEnabled": True}}
    )
    assert info == {
        "token": "t",
        "refresh_token": "r",
        "maintenance": False,
        "cust_id": "62",
        "client_id": "c-1",
        "is_2fa_enabled": True,
    }
    assert parser.login_tokens({"maintenanceMode": True})["maintenance"] is True
    ids = parser.client_ids_from_profile({"statusCode": 200, "data": {"CustID": "62", "SEC_ID": "c-1"}})
    assert ids == {"cust_id": "62", "client_id": "c-1"}
    # Live /users/profile (2026-11-02) returns the profile object unwrapped
    live = parser.client_ids_from_profile({"CustID": "62", "SEC_ID": "c-1", "fullName": "X", "profile_flags": {}})
    assert live == {"cust_id": "62", "client_id": "c-1"}


def test_accounts_from_investment_accounts():
    pairs = parser.accounts_from_investment_accounts(load("investment_accounts.json"))
    assert len(pairs) == 1
    acct, meta = pairs[0]
    assert acct.external_account_id == "acct-0001"
    assert acct.account_name == "NGX Trading (0000000001)"
    assert acct.account_type == "NGX_TRADING"
    assert acct.cash_balance == Decimal("1500.5")
    assert meta["trading_account_no"] == "0100000001"


def test_holdings_mapping_and_enrichment():
    names = {r["secId"]: r for r in load("equities_page0.json")["data"]["content"]}
    holdings = parser.holdings_from_trading_report(load("trading_account_report.json"), names=names)
    assert [h.symbol for h in holdings] == ["FIDSON", "MULTITREX"]
    fid = holdings[0]
    assert fid.security_name == "FIDSON HEALTHCARE PLC"
    assert fid.isin == "NGFIDSON0006"
    assert fid.quantity == Decimal("100")
    assert fid.average_cost == Decimal("80")
    assert fid.cost_basis == Decimal("8000")
    assert fid.current_price == Decimal("83.5")
    assert fid.market_value == Decimal("8350")
    assert fid.unrealized_pnl == Decimal("350")
    assert fid.currency == "NGN"
    assert fid.valuation_timestamp is not None
    # Unknown cost stays None (never coerced to zero); name falls back to secId
    multi = holdings[1]
    assert multi.security_name == "MULTITREX"
    assert multi.cost_basis is None and multi.unrealized_pnl is None


def test_empty_report_is_confirmed_empty():
    assert parser.holdings_from_trading_report(load("trading_account_report_empty.json")) == []


def test_summary_and_cash():
    s = parser.summary_from_trading_report(load("trading_account_report.json"), cash=Decimal("5"))
    assert s.total_market_value == Decimal("9350")
    assert s.total_cost_basis == Decimal("8000")
    assert s.unrealized_pnl == Decimal("1350")
    assert s.cash_value == Decimal("5")
    assert parser.cash_from_wallet_balance({"data": {"balance": 0}, "message": "Success", "statusCode": 200}) == Decimal("0")
    assert parser.cash_from_account_details({"statusCode": 200, "data": {"currentBalance": 12.25}}) == Decimal("12.25")


def test_transactions_skip_balance_rows():
    txns = parser.transactions_from_statement(load("account_statement.json"))
    assert [t.external_transaction_id for t in txns] == ["jrn-1", "jrn-2"]
    assert txns[0].transaction_type == "receipt"
    assert txns[0].total_amount == Decimal("10000")
    assert txns[1].total_amount == Decimal("-8000.5")


def test_deposit_instructions_from_nuban():
    d = parser.deposit_instructions_from_nuban(load("nuban_accounts.json"))
    assert d.bank_name == "Nombank MFB"
    assert d.account_number == "9990000001"
    assert d.account_name == "CARDINALSTONE/TEST CLIENT"
    assert d.provider_reference == "NUBAN-00000-1"
    with pytest.raises(LookupError):
        parser.deposit_instructions_from_nuban({"statusCode": 200, "message": "Success", "data": []})


def test_instruments_filter_search_and_paging():
    rows = load("equities_page0.json")["data"]["content"] + load("equities_page1.json")["data"]["content"]
    res = parser.instruments_result(rows, market_id="NGX", search=None, page=1, per_page=2)
    assert res.total == 3  # suspended row excluded
    assert [i.symbol for i in res.instruments] == ["DANGCEM", "FIDSON"]
    res = parser.instruments_result(rows, market_id="NGX", search="cement", page=1, per_page=50)
    assert [i.symbol for i in res.instruments] == ["DANGCEM"]
    assert res.instruments[0].last_price == Decimal("480")


def test_order_result_and_messages():
    r = parser.order_result_from_response(
        {"statusCode": 201, "message": "Order created", "data": {"id": "o-1", "orderNo": 77, "orderStatus": "NEW", "secId": "FIDSON"}},
        side="buy",
        symbol="FIDSON",
    )
    assert r.external_order_id == "o-1"
    assert r.status == "new"
    assert r.message == "Order created"
    assert parser.broker_message({"error": "Insufficient funds"}) == "Insufficient funds"
    assert parser.qty_from_calculate({"data": {"requestedQty": 12}}) == Decimal("12")
