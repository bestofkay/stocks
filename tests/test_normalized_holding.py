"""NormalizedHolding model tests."""

from __future__ import annotations

from decimal import Decimal

from app.connectors.models import NormalizedHolding


def test_security_identifier_resolution():
    h = NormalizedHolding(security_name="MTN", isin="NGMTNN000001")
    assert h.resolve_security_identifier() == "isin:NGMTNN000001"

    h2 = NormalizedHolding(security_name="MTN", symbol="MTNN", currency="NGN")
    assert h2.resolve_security_identifier() == "symbol:MTNN:NGN"

    h3 = NormalizedHolding(security_name="Cash Sweep")
    assert h3.resolve_security_identifier() == "name:Cash Sweep"


def test_zero_vs_unknown():
    h = NormalizedHolding(security_name="X", quantity=Decimal("0"), current_price=None)
    assert h.quantity == Decimal("0")
    assert h.current_price is None
