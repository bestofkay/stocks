"""Zanibal E-Business Suite (WSTC / SISL) parser + connector tests — no network."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from app.connectors.models import OrderRequest, OrderSide, OrderType
from app.connectors.sisl.connector import SISLConnector
from app.connectors.wstc.connector import WSTCConnector
from app.connectors.zanibal_iw import parser
from app.connectors.zanibal_iw.base import aes_cryptojs_encrypt
from app.connectors.zanibal_iw.html import parse_page
from app.core.errors import AppError

FIX = Path(__file__).parent / "fixtures" / "zanibal_iw"


def page(name: str):
    return parse_page((FIX / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ parsers


def test_aes_matches_portal_cryptojs():
    # Reference value produced by the WSTC login page's own CryptoJS for a dummy password
    assert aes_cryptojs_encrypt("Dummy-Pass#123", "5185536683906693") == "FxUx/a+jTkMTGk9zHVnU9g=="


def test_login_detection():
    assert parser.is_login_page(page("login_wstc.html"))
    assert parser.login_error(page("login_failed.html")) == "Invalid username or password."
    dash = page("dashboard.html")
    assert parser.is_authenticated(dash) and not parser.needs_terms(dash)
    assert parser.customer_number(dash) == "000123"


def test_holdings_summary_and_fixed_income():
    eq = parser.holdings_from_summary(page("summary_per_stock.html"))
    assert [h.symbol for h in eq] == ["DANGCEM", "GTCO"]
    d = eq[0]
    assert d.quantity == Decimal("5") and d.average_cost == Decimal("1499.85")
    assert d.market_value == Decimal("7450.00") and d.current_price == Decimal("1490.00")
    assert d.cost_basis == Decimal("7499.25") and d.unrealized_pnl == Decimal("-49.25")
    fi = parser.fixed_income_from_holdings_page(page("holdings.html"))
    assert len(fi) == 1 and fi[0].asset_type == "treasury_bill"
    assert fi[0].market_value == Decimal("83100.00") and fi[0].quantity == Decimal("100000.00")
    assert fi[0].resolve_security_identifier() == "iw:treasury_bill:TB-77"
    fallback = parser.equities_from_holdings_page(page("holdings.html"))
    assert [h.symbol for h in fallback] == ["DANGCEM"]  # tfoot "Total" row excluded


def test_cash_summary_transactions_instruments():
    st = page("statement.html")
    assert parser.statement_total(st) == Decimal("12500.75")
    assert parser.master_accounts(st) == [("200000001", "Margin Involvement")]
    s = parser.summary_totals(page("summary_per_stock.html"), cash=Decimal("1"))
    assert s.total_market_value == Decimal("19250.00") and s.unrealized_pnl == Decimal("750.75")
    assert s.total_cost_basis == Decimal("18499.25")  # row whose </tr> is missing on the live page
    cash = parser.cash_transactions(page("statement_result.html"), account="A")
    assert [t.total_amount for t in cash] == [Decimal("20000.00"), Decimal("-7499.25")]
    sec = parser.portfolio_transactions(page("portfolio_transactions.html"), account="A")
    assert sec[0].symbol == "DANGCEM" and sec[0].total_amount == Decimal("-7499.25") and sec[0].fees == Decimal("49.25")
    inst = parser.instruments_from_market_stats(page("market_stats.html"), search="dang", page_no=1, per_page=10)
    assert [i.symbol for i in inst.instruments] == ["DANGCEM"] and inst.instruments[0].last_price == Decimal("1490.00")
    tb = parser.trade_balances(page("trade.html"))
    assert tb == {"cash": Decimal("12500.75"), "open_orders": Decimal("0.00"), "available": Decimal("12500.75")}
    lt = '"[{\\u0022LastPrice\\u0022:\\u00221,490.00\\u0022}]"'
    assert parser.last_trade_price(lt) == Decimal("1490.00")


# ---------------------------------------------------------------- connector


class FakePortal:
    def __init__(self, base: str, login_page: str, *, password_expected: str, trade_page="trade.html",
                 preview="order_preview.html"):
        self.base, self.login_page, self.pw = base, login_page, password_expected
        self.trade_page, self.preview = trade_page, preview
        self.logged_in = False
        self.posts: list[tuple[str, dict]] = []

    def html(self, name, status=200):
        return httpx.Response(status, text=(FIX / name).read_text(encoding="utf-8"),
                              headers={"content-type": "text/html; charset=utf-8"})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(self.base)
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()} if request.method == "POST" else {}
        if request.method == "POST":
            self.posts.append((path, form))
        if path == "/Home/Login" and request.method == "GET":
            return self.html(self.login_page)
        if path == "/Home/Login":
            if form.get("UserName") == "user1" and form.get("Password") == self.pw:
                self.logged_in = True
                return httpx.Response(302, headers={"location": f"{self.base}", "set-cookie": "sess=abc; Path=/"})
            return self.html("login_failed.html")
        if not self.logged_in and request.headers.get("cookie", "").find("sess=abc") < 0:
            return httpx.Response(302, headers={"location": f"{self.base}/Home/Login"})
        routes = {"": "dashboard.html", "/CustInfo/AccountStatement": "statement.html",
                  "/CustInfo/ChartSummaryPerStock": "summary_per_stock.html", "/CustInfo/PortfolioHoldings": "holdings.html",
                  "/CustInfo/PortfolioTransactions": "portfolio_transactions.html", "/Research/MarketStats": "market_stats.html",
                  "/Trade/Trade": self.trade_page}
        if request.method == "POST" and path == "/CustInfo/AccountStatement":
            return self.html("statement_result.html")
        if request.method == "POST" and path == "/Trade/Trade":
            return self.html(self.preview)
        if request.method == "POST" and path == "/Trade/ConfirmTrade":
            return self.html("order_done.html")
        if path in routes:
            return self.html(routes[path])
        return httpx.Response(404, text="nf")


def wstc(fake: FakePortal) -> WSTCConnector:
    c = WSTCConnector()
    c.transport = httpx.MockTransport(fake)
    return c


async def test_wstc_login_encrypts_password_and_reads_portfolio():
    fake = FakePortal("/iwnewebiz", "login_wstc.html", password_expected=aes_cryptojs_encrypt("pw", "5185536683906693"))
    c = wstc(fake)
    auth = await c.authenticate({"username": "user1", "password": "pw"})
    assert auth.success and auth.options_patch["api_session"]["cookies"]["sess"] == "abc"
    accts = await c.get_accounts()
    assert accts[0].external_account_id == "200000001" and accts[0].cash_balance == Decimal("12500.75")
    holdings = await c.get_holdings(accts[0])
    assert [h.symbol or h.security_name for h in holdings] == ["DANGCEM", "GTCO", "364-DAY NTB 15-OCT-2027"]
    summary = await c.get_portfolio_summary(accts[0])
    assert summary.total_market_value == Decimal("19250.00") and summary.cash_value == Decimal("12500.75")
    txns = await c.get_transactions(accts[0])
    assert len(txns) == 3
    stmt_post = next(f for p, f in fake.posts if p == "/CustInfo/AccountStatement")
    assert stmt_post["MasterAccount"] == "200000001" and stmt_post["__RequestVerificationToken"] == "stmt-tok"
    inst = await c.list_instruments()
    assert inst.total == 2
    await c.disconnect()


async def test_sisl_posts_plain_password_and_renamed_token():
    fake = FakePortal("/IW", "login_sisl.html", password_expected="pw")
    c = SISLConnector()
    c.transport = httpx.MockTransport(fake)
    auth = await c.authenticate({"username": "user1", "password": "pw"})
    assert auth.success
    login_post = next(f for p, f in fake.posts if p == "/Home/Login")
    assert login_post["__RequestVerificationToken_SKVx5ytUk4"] == "tok-123"
    await c.disconnect()


async def test_bad_password_and_session_resume():
    fake = FakePortal("/iwnewebiz", "login_wstc.html", password_expected="never")
    c = wstc(fake)
    auth = await c.authenticate({"username": "user1", "password": "wrong"})
    assert auth.status == "failed" and "Invalid username" in auth.message
    await c.disconnect()
    fake = FakePortal("/iwnewebiz", "login_wstc.html", password_expected="never")
    c = wstc(fake)
    auth = await c.authenticate({"username": "user1", "password": "x"}, {"api_session": {"cookies": {"sess": "abc"}}})
    assert auth.success and auth.session_metadata["auth_mode"] == "session"
    assert not any(p == "/Home/Login" for p, _ in fake.posts)
    await c.disconnect()


async def test_order_preview_then_confirm():
    fake = FakePortal("/iwnewebiz", "login_wstc.html", password_expected=aes_cryptojs_encrypt("pw", "5185536683906693"))
    c = wstc(fake)
    await c.authenticate({"username": "user1", "password": "pw"})
    res = await c.place_order(OrderRequest(side=OrderSide.BUY, symbol="dangcem", quantity=Decimal("5"),
                                           order_type=OrderType.LIMIT, limit_price=Decimal("1480")))
    assert res.status == "submitted" and res.message == "Order submitted successfully."
    preview = next(f for p, f in fake.posts if p == "/Trade/Trade")
    assert preview["Action"] == "Buy" and preview["Symbol"] == "DANGCEM" and preview["Units"] == "5"
    assert preview["OrderType"] == "Limit" and preview["PriceLimit"] == "1480" and preview["CSCSAcctNumber"] == "CSCS-0001"
    assert preview["__RequestVerificationToken"] == "trade-tok"
    assert preview["DateLimit"] == "2026-11-16" and preview["Instructions"] == "-"
    confirm = next(f for p, f in fake.posts if p == "/Trade/ConfirmTrade")
    assert confirm == {"OrderRef": "PRV-1", "__RequestVerificationToken": "confirm-tok"}
    await c.disconnect()


async def test_order_guards():
    pw = aes_cryptojs_encrypt("pw", "5185536683906693")
    c = wstc(FakePortal("/iwnewebiz", "login_wstc.html", password_expected=pw, trade_page="trade_no_cscs.html"))
    await c.authenticate({"username": "user1", "password": "pw"})
    with pytest.raises(AppError) as exc:
        await c.place_order(OrderRequest(side=OrderSide.BUY, symbol="DANGCEM", quantity=Decimal("1")))
    assert exc.value.status_code == 501
    await c.disconnect()
    c = wstc(FakePortal("/iwnewebiz", "login_wstc.html", password_expected=pw, preview="order_rejected.html"))
    await c.authenticate({"username": "user1", "password": "pw"})
    with pytest.raises(AppError) as exc:
        await c.place_order(OrderRequest(side=OrderSide.BUY, symbol="DANGCEM", quantity=Decimal("1")))
    assert exc.value.status_code == 422 and "Insufficient" in str(exc.value)
    with pytest.raises(AppError):
        await c.place_order(OrderRequest(side=OrderSide.BUY, symbol="NOTREAL", quantity=Decimal("1")))
    await c.disconnect()
