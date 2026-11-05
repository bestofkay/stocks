"""CardinalStone connector flow tests using httpx.MockTransport (no network)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.connectors.cardinalstone.connector import CardinalStoneConnector
from app.connectors.models import OrderRequest, OrderSide, OrderType
from app.core.errors import AppError

FIX = Path(__file__).parent / "fixtures" / "cardinalstone"
CREDS = {"username": "user@example.com", "password": "pw"}


def load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class FakeBroker:
    def __init__(self, *, two_fa: bool = False, valid_token: str = "t1", order_error: str | None = None):
        self.two_fa = two_fa
        self.valid_token = valid_token
        self.order_error = order_error
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.calls.append((request.method, path, body))
        auth = request.headers.get("Authorization", "")
        if path == "/auth/login":
            if body["password"] != "pw":
                return httpx.Response(401, json={"statusCode": 401, "message": "Invalid credentials"})
            return httpx.Response(200, json={
                "token": "t1", "refresh_token": "r1",
                "user": {"CustID": "62", "SEC_ID": "client-0001", "is2FAEnabled": self.two_fa},
            })
        if path == "/auth/refresh":
            if auth == "Bearer r-old":
                return httpx.Response(200, json={"token": "t1", "refresh_token": "r2"})
            return httpx.Response(401, json={"message": "bad refresh"})
        if path == "/auth/verify-2fa":
            ok = body == {"CustID": "62", "token": "123456"}
            return httpx.Response(200 if ok else 400, json={"message": "verified" if ok else "Invalid token"})
        if auth != f"Bearer {self.valid_token}":
            return httpx.Response(401, json={"message": "Unauthorized"})
        if path == "/users/profile":
            return httpx.Response(200, json={"statusCode": 200, "data": {"CustID": "62", "SEC_ID": "client-0001"}})
        if path == "/zanibal/investment-accounts/client-0001":
            return httpx.Response(200, json=load("investment_accounts.json"))
        if path == "/wallet/wallet-balance":
            return httpx.Response(200, json={"data": {"balance": 250}, "message": "Success", "statusCode": 200})
        if path.startswith("/zanibal/trading-account-report/acct-0001/"):
            return httpx.Response(200, json=load("trading_account_report.json"))
        if path == "/zanibal/investment-accounts/details/acct-0001":
            return httpx.Response(200, json={"statusCode": 200, "data": {"currentBalance": 1500.5, "unclearedBalance": 0}})
        if path == "/zanibal/equities":
            page = request.url.params.get("page")
            return httpx.Response(200, json=load(f"equities_page{page}.json"))
        if path == "/wallet/nuban-accounts":
            return httpx.Response(200, json=load("nuban_accounts.json"))
        if path == "/zanibal/orders/calculate":
            return httpx.Response(200, json={"statusCode": 200, "data": {"requestedQty": 12}})
        if path in {"/zanibal/validate-order", "/zanibal/orders"}:
            if self.order_error:
                return httpx.Response(400, json={"statusCode": 400, "message": self.order_error})
            return httpx.Response(200, json={"statusCode": 200, "message": "ok", "data": {"id": "o-1", "orderStatus": "NEW"}})
        return httpx.Response(404, json={"message": "not found"})


def make(broker: FakeBroker) -> CardinalStoneConnector:
    c = CardinalStoneConnector()
    c.transport = httpx.MockTransport(broker)
    return c


async def test_password_login_returns_encrypted_session_patch():
    b = FakeBroker()
    c = make(b)
    auth = await c.authenticate(dict(CREDS))
    assert auth.success and auth.status == "authenticated"
    sess = auth.options_patch["api_session"]
    assert sess["token"] == "t1" and sess["refresh_token"] == "r1" and sess["client_id"] == "client-0001"
    login_body = next(body for m, p, body in b.calls if p == "/auth/login")
    assert login_body["username"] == "user@example.com" and "deviceInfo" in login_body
    await c.disconnect()


async def test_bad_password_fails_cleanly():
    c = make(FakeBroker())
    auth = await c.authenticate({"username": "u", "password": "nope"})
    assert not auth.success and auth.status == "failed"
    assert "Invalid credentials" in auth.message
    await c.disconnect()


async def test_two_factor_required_then_accepted():
    b = FakeBroker(two_fa=True)
    c = make(b)
    auth = await c.authenticate(dict(CREDS))
    assert auth.status == "mfa_required" and auth.options_patch is None
    await c.disconnect()
    c = make(b)
    auth = await c.authenticate({**CREDS, "otp": "123456"})
    assert auth.success and auth.options_patch["api_session"]["token"] == "t1"
    await c.disconnect()


async def test_stored_session_skips_login_and_refreshes():
    b = FakeBroker()
    c = make(b)
    auth = await c.authenticate(dict(CREDS), {"api_session": {"token": "t1", "refresh_token": "r1", "client_id": "client-0001"}})
    assert auth.success and auth.session_metadata["auth_mode"] == "session"
    assert not any(p == "/auth/login" for _, p, _ in b.calls)
    await c.disconnect()

    b = FakeBroker()
    c = make(b)
    auth = await c.authenticate(dict(CREDS), {"api_session": {"token": "expired", "refresh_token": "r-old"}})
    assert auth.success and auth.options_patch["api_session"]["refresh_token"] == "r2"
    assert not any(p == "/auth/login" for _, p, _ in b.calls)
    await c.disconnect()


async def test_portfolio_methods():
    c = make(FakeBroker())
    await c.authenticate(dict(CREDS))
    accounts = await c.get_accounts()
    assert [a.external_account_id for a in accounts] == ["acct-0001", "wallet:NGN"]
    assert accounts[1].cash_balance == Decimal("250")
    holdings = await c.get_holdings(accounts[0])
    assert holdings[0].security_name == "FIDSON HEALTHCARE PLC"
    assert await c.get_holdings(accounts[1]) == []
    assert await c.get_cash_balance(accounts[0]) == Decimal("1500.5")
    summary = await c.get_portfolio_summary(accounts[0])
    assert summary.total_market_value == Decimal("9350") and summary.cash_value == Decimal("1500.5")
    dep = await c.get_deposit_instructions()
    assert dep.account_number == "9990000001"
    inst = await c.list_instruments(search="multi")
    assert [i.symbol for i in inst.instruments] == ["MULTITREX"]
    await c.disconnect()


async def test_limit_order_validates_then_places():
    b = FakeBroker()
    c = make(b)
    await c.authenticate(dict(CREDS))
    res = await c.place_order(OrderRequest(side=OrderSide.BUY, symbol="fidson", quantity=Decimal("10"),
                                           order_type=OrderType.LIMIT, limit_price=Decimal("80")))
    assert res.external_order_id == "o-1" and res.status == "new"
    posts = [(p, body) for m, p, body in b.calls if m == "POST" and p.startswith("/zanibal/")]
    assert [p for p, _ in posts] == ["/zanibal/validate-order", "/zanibal/orders"]
    body = posts[1][1]
    assert body["secId"] == "FIDSON" and body["side"] == "BUY" and body["requestedQty"] == 10
    assert body["limitPrice"] == 80.0 and body["tradingAccountNo"] == "0100000001"
    assert body["accountId"] == "acct-0001" and body["assetType"] == "EQUITY"
    await c.disconnect()


async def test_market_amount_order_uses_calculate():
    b = FakeBroker()
    c = make(b)
    await c.authenticate(dict(CREDS))
    await c.place_order(OrderRequest(side=OrderSide.SELL, symbol="FIDSON", amount=Decimal("1000")))
    paths = [p for m, p, _ in b.calls if m == "POST"]
    assert paths[-3:] == ["/zanibal/orders/calculate", "/zanibal/validate-order", "/zanibal/orders"]
    body = b.calls[-1][2]
    assert body["requestedQty"] == 12 and "limitPrice" not in body and body["side"] == "SELL"
    await c.disconnect()


async def test_broker_rejection_surfaces_message():
    c = make(FakeBroker(order_error="Insufficient funds for order"))
    await c.authenticate(dict(CREDS))
    with pytest.raises(AppError) as exc:
        await c.place_order(OrderRequest(side=OrderSide.BUY, symbol="FIDSON", quantity=Decimal("1"),
                                         order_type=OrderType.LIMIT, limit_price=Decimal("80")))
    assert exc.value.status_code == 422 and "Insufficient funds" in str(exc.value)
    await c.disconnect()


async def test_requests_outside_allow_list_blocked():
    c = make(FakeBroker())
    await c.authenticate(dict(CREDS))
    with pytest.raises(AppError):
        await c._get("https://evil.example.com/steal")
    await c.disconnect()
