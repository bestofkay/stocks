"""InvestNow parser + connector-logic tests (no browser, no network)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.connectors.investnow import parser, selectors
from app.connectors.investnow.connector import InvestNowConnector
from app.core.errors import AppError

FIX = Path(__file__).parent / "fixtures" / "investnow"


def load(n):
    return json.loads((FIX / n).read_text(encoding="utf-8"))


def test_live_shapes_parse_to_empty():
    e = parser.portfolio_entries(load("portfolio_items_live.json"))
    assert e == [] and parser.accounts(e) == []
    assert parser.securities_present(load("portfolio_items_live.json")) is False
    assert parser.transactions(load("invoice_history_live.json")) == []
    assert parser.portfolio_entries(load("session_expired_live.json")) == []


def test_fund_and_placement_entries():
    e = parser.portfolio_entries(load("portfolio_items_funds.json"))
    accts = parser.accounts(e)
    assert [a.external_account_id for a in accts] == ["MMF-001", "WM-009"]
    assert accts[0].account_type == "MUTUAL_FUND" and accts[0].cash_balance == Decimal("1500")
    mmf = parser.holdings_for(e, "MMF-001")[0]
    assert mmf.market_value == Decimal("105000.5") and mmf.cost_basis == Decimal("100000")
    assert mmf.unrealized_pnl == Decimal("5000.5") and mmf.asset_type == "mutual_fund"
    fd = parser.holdings_for(e, "WM-009")[0]
    assert fd.security_name == "Fixed Deposit 91 Days" and fd.symbol == "FD91"
    assert fd.market_value == Decimal("204100") and fd.unrealized_pnl == Decimal("4100")
    s = parser.summary_for(e, "WM-009")
    assert s.total_market_value == Decimal("204100") and s.total_cost_basis == Decimal("200000")


def test_transactions():
    t = parser.transactions(load("invoices.json"), {"data": [{"id": "w1", "amount": 5000, "createdAt": "2026-10-25T10:00:00Z"}]})
    assert [x.external_transaction_id for x in t] == ["deposit:inv-1", "withdrawal:w1"]
    assert t[0].total_amount == Decimal("100000") and t[1].total_amount == Decimal("-5000")


class Captured(InvestNowConnector):
    """Connector with the browser capture replaced by fixture data."""

    def __init__(self, captured):
        super().__init__()
        self._authenticated = True
        self._page = object()
        self._fixture = captured

    async def _collect(self):
        self._require_auth()
        return self._fixture


async def test_connector_methods_over_captured_data():
    c = Captured({selectors.PATH_PORTFOLIO_ITEMS: load("portfolio_items_funds.json"),
                  selectors.PATH_INVOICES: load("invoices.json")})
    accts = await c.get_accounts()
    assert len(accts) == 2
    assert len(await c.get_holdings(accts[0])) == 1
    assert await c.get_cash_balance(accts[0]) == Decimal("1500")
    assert (await c.get_portfolio_summary(accts[1])).total_market_value == Decimal("204100")
    assert len(await c.get_transactions(accts[0])) == 1


async def test_requires_auth_and_metadata():
    c = InvestNowConnector()
    with pytest.raises(AppError):
        await c.get_accounts()
    meta = InvestNowConnector.metadata()
    assert meta.connector_type.value == "browser_automation" and meta.operational is False
    assert "orders" not in [x.value for x in meta.capabilities]
