"""Statement import helper tests."""

from __future__ import annotations

from decimal import Decimal

from app.connectors.statement_base import holdings_from_csv, parse_decimal_safe


CSV = """symbol,name,quantity,market_value,currency
MTNN,MTN Nigeria,1000,220000,NGN
DANGCEM,Dangote Cement,500,,NGN
"""


def test_parse_decimal_safe():
    assert parse_decimal_safe("1,000.5") == Decimal("1000.5")
    assert parse_decimal_safe("") is None
    assert parse_decimal_safe(None) is None


def test_holdings_from_csv_preserves_unknown_value():
    holdings = holdings_from_csv(CSV)
    assert len(holdings) == 2
    mtnn = holdings[0]
    assert mtnn.symbol == "MTNN"
    assert mtnn.quantity == Decimal("1000")
    assert mtnn.market_value == Decimal("220000")
    dang = holdings[1]
    assert dang.market_value is None  # missing must stay None, not zero
