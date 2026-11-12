"""InvestNaija parser tests (anonymized fixtures, no live credentials)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.connectors.investnaija import parser

FIX = Path(__file__).parent / "fixtures" / "investnaija"


def load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_login_tokens():
    info = parser.login_tokens(load("login.json"))
    assert info["token"].startswith("eyJ")
    assert info["uuid_token"] == "ac4d75e63491uuid"
    assert info["two_factor"] is False
    assert info["profile_id"] == "fe8bb0b4-1abb-4e9c-9af5-a1df069d3185"


def test_trade_accounts_and_holdings():
    rows = parser.trade_portfolio_rows(load("trade_portfolios.json"))
    assert len(rows) == 1
    acct, meta = parser.account_from_trade_portfolio(rows[0])
    assert acct.external_account_id == "portfolio-token-1"
    assert acct.account_name == "Okunuga Soji"
    assert meta["signature"] == "sig-token-1"
    assert meta["available_cash"] == Decimal("3598.55")
    holdings = parser.holdings_from_trade_portfolio(rows[0])
    assert len(holdings) == 1
    h = holdings[0]
    assert h.symbol == "ACCESSCORP"
    assert h.quantity == Decimal("50")
    assert h.current_price == Decimal("27")
    assert h.market_value == Decimal("1350")


def test_deposit_instructions_from_wallet():
    d = parser.deposit_instructions_from_wallet(load("wallet_balance.json"))
    assert d.account_number == "6244352498"
    assert d.bank_name == "Wema Bank"
    assert d.currency == "NGN"


def test_instruments_from_securities():
    rows = parser.securities_rows(load("securities_sample.json"))
    result = parser.instruments_result(rows, market_id="NGX", search="jaiz", page=1, per_page=10)
    assert result.total == 1
    assert result.instruments[0].symbol == "JAIZBANK"
    assert result.instruments[0].last_price == Decimal("7.65")
