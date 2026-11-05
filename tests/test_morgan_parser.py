"""Unit tests for Morgan Capital parsers (no live credentials)."""

from __future__ import annotations

from decimal import Decimal

from app.connectors.morgan.parser import (
    account_from_portal_text,
    cash_from_wallet_text,
    deposit_instructions_from_wallet_text,
    holdings_from_portfolio_text,
    instruments_from_markets_text,
    order_result_from_payload,
    parse_itrade_auth_body,
)
from app.connectors.registry import clear_registry_for_tests, discover_connectors, get_provider_metadata


DASHBOARD = """
SO
Soji Oriyomi Okunuga
CSCS pending
Sign out
TOTAL PORTFOLIO VALUE
₦0.00
WALLET · WEMA
● INSTANT
₦0.00
Reserved ₦0 · Acct —
No holdings yet
"""

WALLET_KYC = """
Wallet
Powered by Wema · NDIC insured
AVAILABLE BALANCE
₦0.00
YOUR VIRTUAL ACCOUNT
Your personal funding account is issued the moment your KYC is approved
Finish KYC →
"""

WALLET_VA = """
Soji Oriyomi Okunuga
CSCS pending
AVAILABLE BALANCE
₦1,234.50
YOUR VIRTUAL ACCOUNT
9995 911 821
Wema Bank · MorganCapital/Soji Oriyomi Okunuga
⧉ Copy account number
"""

MARKETS = """
Top gainers
SE
SEPLAT
Oil & Gas
₦5,890.00
+4.10%
ZE
ZENITHBANK
Banking
₦68.20
+2.87%
DC
DANGCEM
Industrial
₦702.00
+2.34%
"""

PORTFOLIO_EMPTY = """
Portfolio
0 asset classes · 0 positions
No holdings yet
Your Nigerian portfolio starts here
"""


def test_morgan_registered_operational():
    clear_registry_for_tests()
    discover_connectors()
    meta = get_provider_metadata("morgan")
    assert meta.provider_name == "Morgan Capital"
    assert meta.implementation_status.value == "operational"
    assert meta.operational is True
    assert "morgancapitalgroup.com" in meta.approved_domains


def test_portal_account_and_cash():
    acct = account_from_portal_text(DASHBOARD)
    assert acct.external_account_id == "morgan-primary"
    assert "Soji" in acct.account_name
    assert cash_from_wallet_text(WALLET_VA) == Decimal("1234.50")


def test_empty_holdings_and_markets():
    assert holdings_from_portfolio_text(PORTFOLIO_EMPTY) == []
    instruments = instruments_from_markets_text(MARKETS)
    symbols = {i.symbol for i in instruments.instruments}
    assert "SEPLAT" in symbols
    assert "ZENITHBANK" in symbols
    assert instruments.instruments[0].last_price == Decimal("5890.00")


def test_deposit_kyc_gate_and_va():
    try:
        deposit_instructions_from_wallet_text(WALLET_KYC)
        assert False, "expected KYC ValueError"
    except ValueError as exc:
        assert "KYC" in str(exc)
    dep = deposit_instructions_from_wallet_text(WALLET_VA)
    assert dep.account_number == "9995911821"
    assert dep.bank_name and "Wema" in dep.bank_name
    assert dep.account_name and "Soji" in dep.account_name


def test_parse_itrade_auth_bodies_legacy():
    ok, msg = parse_itrade_auth_body("errmsg||Invalid Login ID and/or Password")
    assert ok is False
    ok, msg = parse_itrade_auth_body("OK||2")
    assert ok is True


def test_order_result_helper():
    order = order_result_from_payload(
        {"order_id": "MC-1", "status": "submitted"}, side="buy", symbol="SEPLAT"
    )
    assert order.external_order_id == "MC-1"
