"""Fundvine parser tests (anonymized fixtures, no live credentials)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.connectors.fundvine import parser

FIX = Path(__file__).parent / "fixtures" / "fundvine"


def load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_cscs_accounts():
    rows = parser.cscs_accounts(load("cscs_accounts.json"))
    assert len(rows) == 1
    acct, meta = parser.account_from_cscs(rows[0])
    assert acct.external_account_id == "0138054839"
    assert acct.account_name == "Test Client"
    assert meta["client_id"] == 2916
    assert meta["can_trade"] is True


def test_holdings_and_cash_from_positions():
    pos = load("stock_positions.json")
    holdings = parser.holdings_from_positions(pos)
    assert len(holdings) == 1
    h = holdings[0]
    assert h.symbol == "GTCO"
    assert h.quantity == Decimal("100")
    assert h.current_price == Decimal("45.0")
    assert h.market_value == Decimal("4500")
    assert parser.cash_from_positions(pos) == Decimal("1250.5")
    summary = parser.summary_from_positions(pos, cash=Decimal("1250.5"))
    assert summary.total_market_value == Decimal("4500")
    assert summary.cash_value == Decimal("1250.5")


def test_transactions_from_statements():
    txs = parser.transactions_from_statements(load("account_statements.json"))
    assert len(txs) == 1
    assert txs[0].total_amount == Decimal("1000")
    assert txs[0].transaction_type == "credit"


def test_deposit_instructions_from_attributes():
    d = parser.deposit_instructions_from_attributes(
        load("account_attributes.json"),
        funding_url="https://etrade.fundvinesecurities.com/Client/Deposit.aspx",
    )
    assert d.account_number == "0138054839"
    assert d.account_name == "Test Client"
    assert "Paystack" in (d.bank_name or "")
    assert d.raw and d.raw.get("funding_method") == "paystack_card"


def test_instruments_from_securities():
    rows = parser.securities_rows(load("securities_sample.json"))
    assert len(rows) >= 1
    result = parser.instruments_result(rows, market_id="NGX", search="abbey", page=1, per_page=10)
    assert result.total >= 1
    assert result.instruments[0].symbol
