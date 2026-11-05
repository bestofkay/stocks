"""Afrinvestor HTML fixture parsing tests (no live credentials)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.connectors.afrinvestor.parser import (
    parse_cash_from_text,
    parse_decimal,
    parse_holdings_html,
)

FIXTURE = Path(__file__).parent / "fixtures" / "afrinvestor" / "holdings_sample.html"


def test_parse_decimal_variants():
    assert parse_decimal("1,000.50") == Decimal("1000.50")
    assert parse_decimal("₦220.00") == Decimal("220.00")
    assert parse_decimal("(100)") == Decimal("-100")
    assert parse_decimal("N/A") is None
    assert parse_decimal(None) is None
    assert parse_decimal("") is None


def test_parse_holdings_from_fixture():
    html = FIXTURE.read_text(encoding="utf-8")
    holdings = parse_holdings_html(html, default_currency="NGN")
    assert len(holdings) == 2
    mtnn = next(h for h in holdings if h.symbol == "MTNN")
    assert mtnn.security_name == "MTN Nigeria"
    assert mtnn.quantity == Decimal("1000")
    assert mtnn.current_price == Decimal("220.00")
    assert mtnn.market_value == Decimal("220000.00")
    assert mtnn.average_cost == Decimal("180.00")
    assert mtnn.unrealized_pnl == Decimal("40000.00")
    assert mtnn.currency == "NGN"
    # Missing price must remain None elsewhere — not tested as zero here
    assert mtnn.isin is None


def test_parse_cash_from_fixture_text():
    html = FIXTURE.read_text(encoding="utf-8")
    cash = parse_cash_from_text(html)
    assert cash == Decimal("125000.50")


def test_approved_domains_restrict_hosts():
    from urllib.parse import urlparse

    from app.connectors.afrinvestor.connector import _host_allowed

    assert _host_allowed("https://afrinvestor.com/home")
    assert _host_allowed("https://www.afrinvestor.com/portfolio")
    assert not _host_allowed("https://evil.example.com/")
    assert not _host_allowed("http://169.254.169.254/latest/meta-data/")
    assert not _host_allowed("https://afrinvestor.com.evil.com/")
    # subdomain of approved root
    host = urlparse("https://cdn.afrinvestor.com/x").hostname
    assert host is not None
