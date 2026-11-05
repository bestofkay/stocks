"""Vetiva connector flow tests using httpx.MockTransport (no network)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.connectors.models import OrderRequest, OrderSide, OrderType
from app.connectors.vetiva.connector import VetivaConnector
from app.core.errors import AppError

FIX = Path(__file__).parent / "fixtures" / "vetiva"
CREDS = {"username": "user@example.com", "password": "pw"}


def load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def session(token: str = "a1", refresh: str = "r1") -> dict:
    return {"access_token": token, "refresh_token": refresh, "account_id": "acc-1"}


class FakeVetiva:
    def __init__(self, *, two_fa=False, trade_account=True, valid_token="a1", order_error=None):
        self.two_fa, self.trade_account, self.valid_token, self.order_error = two_fa, trade_account, valid_token, order_error
        self.calls: list[tuple[str, str, dict | None, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path.removeprefix("/api/proxy")
        self.calls.append((request.method, path, body, dict(request.headers)))
        if path == "/auth/login":
            if body != {"email": "user@example.com", "password": "pw", "channel": "web"}:
                return httpx.Response(404, json={"error": "No Account Found For Email", "status_code": 404})
            data = {"is2faenabled": self.two_fa}
            if not self.two_fa:
                data["session"] = session()
            return httpx.Response(200, json={"message": "ok", "status_code": 200, "data": data})
        if path == "/auth/verify-2fa":
            ok = body == {"channel": "web", "email": "user@example.com", "token": "123456", "password": "pw"}
            if not ok:
                return httpx.Response(400, json={"error": "Invalid or expired code", "status_code": 400})
            return httpx.Response(200, json={"message": "Verified", "status_code": 200, "data": {"session": session()}})
        if path == "/auth/refresh":
            if body == {"refresh_token": "r-old"}:
                return httpx.Response(200, json={"status_code": 200, "data": {"session": session("a1", "r2")}})
            return httpx.Response(401, json={"error": "bad refresh", "status_code": 401})
        if request.headers.get("authorization") != f"Bearer {self.valid_token}":
            return httpx.Response(401, json={"message": "Session expired. Please login again.", "status_code": 401})
        if path == "/account/individual":
            return httpx.Response(200, json={"status_code": 200, "data": {"account_id": "acc-1", "has_trade_account": self.trade_account}})
        if path == "/portfolio/value":
            return httpx.Response(200, json=load("portfolio_value.json"))
        if path == "/portfolio/assets":
            return httpx.Response(200, json=load("portfolio_assets.json"))
        if path == "/portfolio/breakdown-expanded":
            return httpx.Response(200, json=load("breakdown_expanded.json"))
        if path == "/payment/get-account-wallets":
            return httpx.Response(200, json=load("wallets.json"))
        if path == "/payment/get-wallet-transactions":
            return httpx.Response(200, json={"status_code": 200, "message": "wallet transactions fetched!", "data": []})
        if path == "/vsl/trading-account/transactions":
            return httpx.Response(200, json=load("trading_transactions.json"))
        if path == "/vsl/list-securities":
            return httpx.Response(200, json=load("securities.json"))
        if path == "/vsl/get-sec-stats":
            return httpx.Response(200, json={"status_code": 200, "data": {"lastPx": 1420}})
        if path in {"/vsl/order/fee-preview", "/vsl/order"}:
            if self.order_error:
                return httpx.Response(400, json={"error": self.order_error, "reason": "rpc", "status_code": 400})
            if path == "/vsl/order":
                return httpx.Response(200, json={"message": "Order placed", "status_code": 200, "data": {"order_id": "o-1", "queued": False}})
            return httpx.Response(200, json={"status_code": 200, "data": {"total": "14300"}})
        return httpx.Response(404, json={"message": "404 page not found\n"})


def make(fake: FakeVetiva) -> VetivaConnector:
    c = VetivaConnector()
    c.transport = httpx.MockTransport(fake)
    return c


async def test_login_stores_session_and_profile():
    fake = FakeVetiva()
    c = make(fake)
    auth = await c.authenticate(dict(CREDS))
    assert auth.success and auth.options_patch["api_session"]["token"] == "a1"
    assert auth.options_patch["api_session"]["refresh_token"] == "r1"
    assert auth.session_metadata["has_trade_account"] is True
    await c.disconnect()


async def test_unknown_email_fails():
    c = make(FakeVetiva())
    auth = await c.authenticate({"username": "x@example.com", "password": "pw"})
    assert not auth.success and auth.status == "failed" and "No Account Found" in auth.message
    await c.disconnect()


async def test_two_fa_email_code_flow_does_not_relogin():
    fake = FakeVetiva(two_fa=True)
    c = make(fake)
    auth = await c.authenticate(dict(CREDS))
    assert auth.status == "mfa_required" and auth.options_patch is None
    await c.disconnect()
    fake.calls.clear()
    c = make(fake)
    auth = await c.authenticate({**CREDS, "otp": "123456"})
    assert auth.success
    assert not any(p == "/auth/login" for _, p, _, _ in fake.calls)  # avoids emailing a new code
    await c.disconnect()


async def test_stored_session_and_refresh():
    fake = FakeVetiva()
    c = make(fake)
    auth = await c.authenticate(dict(CREDS), {"api_session": {"token": "a1", "refresh_token": "r1", "account_id": "acc-1"}})
    assert auth.success and auth.session_metadata["auth_mode"] == "session"
    assert not any(p == "/auth/login" for _, p, _, _ in fake.calls)
    await c.disconnect()
    fake = FakeVetiva()
    c = make(fake)
    auth = await c.authenticate(dict(CREDS), {"api_session": {"token": "expired", "refresh_token": "r-old"}})
    assert auth.success and auth.options_patch["api_session"]["refresh_token"] == "r2"
    await c.disconnect()


async def test_portfolio_methods():
    c = make(FakeVetiva())
    await c.authenticate(dict(CREDS))
    accts = await c.get_accounts()
    assert [a.external_account_id for a in accts] == ["portfolio:acc-1", "wallet:w-1"]
    holdings = await c.get_holdings(accts[0])
    assert {h.symbol for h in holdings} == {"DANGCEM", None}
    assert await c.get_holdings(accts[1]) == []
    assert await c.get_cash_balance(accts[0]) == Decimal("1200.5")
    assert await c.get_cash_balance(accts[1]) == Decimal("250.00")
    s = await c.get_portfolio_summary(accts[0])
    assert s.total_market_value == Decimal("15000") and s.unrealized_pnl == Decimal("1200")
    txns = await c.get_transactions(accts[0])
    assert len(txns) == 2 and await c.get_transactions(accts[1]) == []
    inst = await c.list_instruments(search="abbey")
    assert [i.symbol for i in inst.instruments] == ["ABBEYBANK"]
    await c.disconnect()


async def test_limit_order_preview_then_place():
    fake = FakeVetiva()
    c = make(fake)
    await c.authenticate(dict(CREDS))
    res = await c.place_order(OrderRequest(side=OrderSide.BUY, symbol="dangcem", quantity=Decimal("10"),
                                           order_type=OrderType.LIMIT, limit_price=Decimal("1400.50")))
    assert res.external_order_id == "o-1" and res.status == "submitted"
    posts = [(p, b, h) for m, p, b, h in fake.calls if m == "POST" and p.startswith("/vsl/")]
    assert [p for p, _, _ in posts] == ["/vsl/order/fee-preview", "/vsl/order"]
    body = posts[1][1]
    assert body == {"type": "2", "symbol": "DANGCEM", "qty": 10, "side": "1", "tif": "0", "px": "1400.50"}
    assert posts[1][2].get("idempotency-key")
    await c.disconnect()


async def test_market_amount_sell_uses_last_price():
    fake = FakeVetiva()
    c = make(fake)
    await c.authenticate(dict(CREDS))
    await c.place_order(OrderRequest(side=OrderSide.SELL, symbol="DANGCEM", amount=Decimal("10000")))
    body = [b for m, p, b, _ in fake.calls if p == "/vsl/order"][0]
    assert body == {"type": "1", "symbol": "DANGCEM", "qty": 7, "side": "2", "tif": "0"}
    await c.disconnect()


async def test_orders_locked_without_trade_account():
    c = make(FakeVetiva(trade_account=False))
    await c.authenticate(dict(CREDS))
    with pytest.raises(AppError) as exc:
        await c.place_order(OrderRequest(side=OrderSide.BUY, symbol="DANGCEM", quantity=Decimal("1")))
    assert exc.value.status_code == 501
    assert await c.get_transactions((await c.get_accounts())[0]) == []
    await c.disconnect()


async def test_broker_rejection_and_allow_list():
    c = make(FakeVetiva(order_error="Insufficient trading account balance"))
    await c.authenticate(dict(CREDS))
    with pytest.raises(AppError) as exc:
        await c.place_order(OrderRequest(side=OrderSide.BUY, symbol="DANGCEM", quantity=Decimal("1")))
    assert exc.value.status_code == 422 and "Insufficient" in str(exc.value)
    with pytest.raises(AppError):
        await c._get("https://evil.example.com/x")
    await c.disconnect()
