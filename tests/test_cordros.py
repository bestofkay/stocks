"""Cordros CAPE parser + connector tests (no network)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.connectors.cordros import parser
from app.connectors.cordros.connector import CordrosConnector
from app.connectors.models import OrderRequest, OrderSide, OrderType
from app.core.errors import AppError

FIX = Path(__file__).parent / "fixtures" / "cordros"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_customer_selection_and_no_customer():
    cust = parser.customer_from_user(load("login_with_customer.json")["user"])
    assert cust["st_id"] == "ST-7" and cust["cscs"] == "CSCS-123" and cust["customer_id"] == "501"
    assert parser.customer_from_user(parser.user_from_profile(load("profile_no_customer.json"))) is None


def test_portfolio_rows_and_totals():
    rows = parser.gateway_rows(load("portfolio.json"), "GetStockPortfolio/CSCS-123")
    h = parser.holdings_from_portfolio(rows)
    assert [x.symbol for x in h] == ["DANGCEM", "GTCO"]  # CASH / TOTAL / GRAND TOTAL rows excluded
    assert h[0].quantity == Decimal("10") and h[0].average_cost == Decimal("1400") and h[0].current_price == Decimal("1490")
    assert h[0].unrealized_pnl == Decimal("900") and h[1].unrealized_pnl == Decimal("-200")
    tot = parser.portfolio_totals(rows)
    assert tot == {"cash": Decimal("25000.5"), "assets": Decimal("26700"), "grand_total": Decimal("51700.5")}
    s = parser.summary(rows, cash=None)
    assert s.total_market_value == Decimal("26700") and s.total_cost_basis == Decimal("26000")
    assert s.unrealized_pnl == Decimal("700") and s.cash_value == Decimal("25000.5")
    assert parser.gateway_rows(load("no_data.json"), "GetStockPortfolio/") is None


def test_accounts_cash_trades():
    pairs = parser.market_accounts(parser.gateway_rows(load("market_accounts.json"), "GetMarketAccounts/ST-7"))
    assert pairs[0][0].external_account_id == "700123" and pairs[0][1]["market_id"] == "NSE1"
    assert parser.cash_balance(parser.gateway_rows(load("cash_balance.json"), "/GetCashBalance/700123")) == Decimal("25000.5")
    t = parser.trades(parser.gateway_rows(load("trades.json"), "GetTradeByPostDate/700123/a/b"), account="700123")
    assert [x.transaction_type for x in t] == ["buy", "sell"]
    assert t[0].total_amount == Decimal("-14075.5") and t[1].total_amount == Decimal("580")


def test_deposit_instructions_from_live_html():
    d = parser.deposit_from_config(load("portfolio_configuration.json"))
    assert d.account_name == "CORDROS SECURITIES LIMITED"
    assert d.account_number == "0000460692" and d.bank_name == "STANBIC IBTC BANK"
    assert any("3rd-party" in n for n in d.raw["notes"])


def test_instruments():
    rows = parser.gateway_rows(load("prices.json"), "GetMarketStockPrices")
    res = parser.instruments(rows, search=None, page=1, per_page=10)
    assert [i.symbol for i in res.instruments] == ["CONHALLPLC", "DANGCEM", "VETINDETF"]
    assert res.instruments[2].asset_type == "etf" and res.instruments[1].last_price == Decimal("1490")
    assert [i.symbol for i in parser.instruments(rows, search="dangote", page=1, per_page=10).instruments] == ["DANGCEM"]


class FakeCordros:
    def __init__(self, *, customer=True, order_reply=None):
        self.customer, self.order_reply = customer, order_reply
        self.calls = []

    def __call__(self, req: httpx.Request):
        body = json.loads(req.content) if req.content else None
        path = req.url.path.removeprefix("/api/v1/")
        self.calls.append((req.method, path, body))
        if path == "auth/login":
            if body != {"email": "user@example.com", "password": "pw"}:
                return httpx.Response(401, json={"message": "Unauthorized"})
            data = load("login_with_customer.json")
            if not self.customer:
                data["user"]["customer"] = None
            return httpx.Response(200, json=data)
        if req.headers.get("authorization") != "Bearer tok-1":
            return httpx.Response(401, json={"message": "Unauthenticated."})
        if path == "auth/user-profile":
            p = load("profile_no_customer.json")
            if self.customer:
                p["data"]["customer"] = load("login_with_customer.json")["user"]["customer"]
            return httpx.Response(200, json=p)
        if path == "securities/portfolio-configuration":
            return httpx.Response(200, json=load("portfolio_configuration.json"))
        if path == "general-stock-api":
            ep = body["endpoint"]
            if ep.startswith("GetMarketAccounts/"):
                return httpx.Response(200, json=load("market_accounts.json"))
            if ep.startswith("/GetCashBalance/"):
                return httpx.Response(200, json=load("cash_balance.json"))
            if ep.startswith("GetStockPortfolio/"):
                return httpx.Response(200, json=load("portfolio.json"))
            if ep.startswith("GetTradeByPostDate/"):
                return httpx.Response(200, json=load("trades.json"))
            if ep == "GetMarketStockPrices":
                return httpx.Response(200, json=load("prices.json"))
            if ep.startswith("GetMarketStockPrice/"):
                p = load("prices.json"); p["data"]["result"] = [{"GetMarketStockPrice": [p["data"]["result"][0]["GetMarketStockPrices"][1]]}]
                return httpx.Response(200, json=p)
            if ep in ("/DoBuyOrder/", "/DoSellOrder/"):
                return httpx.Response(200, json=self.order_reply or {"success": True, "message": "Order submitted", "data": None})
        return httpx.Response(404, json={"message": "nf"})


def make(fake):
    c = CordrosConnector(); c.transport = httpx.MockTransport(fake); return c


async def test_full_flow_with_customer():
    fake = FakeCordros()
    c = make(fake)
    auth = await c.authenticate({"username": "User@Example.com", "password": "pw"})  # lower-cased like the web app
    assert auth.success and auth.session_metadata["has_customer_record"] is True
    assert auth.options_patch["api_session"]["token"] == "tok-1"
    accts = await c.get_accounts()
    assert accts[0].external_account_id == "700123" and accts[0].cash_balance == Decimal("25000.5")
    assert len(await c.get_holdings(accts[0])) == 2
    s = await c.get_portfolio_summary(accts[0])
    assert s.total_market_value == Decimal("26700") and s.cash_value == Decimal("25000.5")
    assert len(await c.get_transactions(accts[0])) == 2
    assert (await c.get_deposit_instructions()).account_number == "0000460692"
    assert (await c.list_instruments(search="hallmark")).instruments[0].symbol == "CONHALLPLC"
    await c.disconnect()


async def test_orders_payload_and_amount():
    fake = FakeCordros()
    c = make(fake)
    await c.authenticate({"username": "user@example.com", "password": "pw"})
    r = await c.place_order(OrderRequest(side=OrderSide.BUY, symbol="dangcem", quantity=Decimal("10"),
                                         order_type=OrderType.LIMIT, limit_price=Decimal("1480.5")))
    assert r.status == "submitted" and r.message == "Order submitted"
    sent = [b for m, p, b in fake.calls if p == "general-stock-api" and b["endpoint"] == "/DoBuyOrder/"][0]
    assert sent["platform"] == "ST" and sent["payload"]["buyorder"] == [{
        "market_id": "NSE1", "market_accountid": "700123", "symbol_code": "DANGCEM", "order_quantity": "10",
        "order_type": "L", "limit_price": "1480.5", "time_in_force": "DAY", "expiry_date": ""}]
    await c.place_order(OrderRequest(side=OrderSide.SELL, symbol="DANGCEM", amount=Decimal("5000")))
    sell = [b for m, p, b in fake.calls if p == "general-stock-api" and b["endpoint"] == "/DoSellOrder/"][0]
    assert sell["payload"]["sellorder"][0]["order_quantity"] == "3" and sell["payload"]["sellorder"][0]["order_type"] == "M"
    await c.disconnect()


async def test_no_customer_record_kyc_pending():
    c = make(FakeCordros(customer=False))
    auth = await c.authenticate({"username": "user@example.com", "password": "pw"})
    assert auth.success and auth.session_metadata["has_customer_record"] is False
    assert await c.get_accounts() == []
    assert (await c.get_deposit_instructions()).bank_name == "STANBIC IBTC BANK"  # works pre-KYC
    with pytest.raises(AppError) as exc:
        await c.place_order(OrderRequest(side=OrderSide.BUY, symbol="DANGCEM", quantity=Decimal("1")))
    assert exc.value.status_code == 501
    await c.disconnect()


async def test_rejections_and_session_resume():
    c = make(FakeCordros(order_reply={"success": False, "message": "Insufficient balance"}))
    await c.authenticate({"username": "user@example.com", "password": "pw"})
    with pytest.raises(AppError) as exc:
        await c.place_order(OrderRequest(side=OrderSide.BUY, symbol="DANGCEM", quantity=Decimal("1")))
    assert exc.value.status_code == 422 and "Insufficient" in str(exc.value)
    await c.disconnect()
    fake = FakeCordros()
    c = make(fake)
    auth = await c.authenticate({"username": "x", "password": "y"}, {"api_session": {"token": "tok-1"}})
    assert auth.success and auth.session_metadata["auth_mode"] == "session"
    assert not any(p == "auth/login" for _, p, _ in fake.calls)
    bad = await make(FakeCordros()).authenticate({"username": "user@example.com", "password": "nope"})
    assert bad.status == "failed"
    await c.disconnect()
