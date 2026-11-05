"""Decimal portfolio aggregation and duplicate-holding logic (pure unit)."""

from __future__ import annotations

from decimal import Decimal

from app.schemas.portfolio import HoldingResponse, SecurityAggregateResponse


def _holding(**kwargs) -> HoldingResponse:
    defaults = dict(
        id=1,
        account_id=1,
        connection_id=1,
        provider_id="afrinvestor",
        account_name="Main",
        security_identifier="symbol:MTNN:NGN",
        symbol="MTNN",
        security_name="MTN Nigeria",
        isin=None,
        asset_type="equity",
        quantity=Decimal("1000"),
        average_cost=None,
        cost_basis=None,
        current_price=None,
        market_value=Decimal("220000"),
        unrealized_pnl=None,
        currency="NGN",
        valuation_timestamp=None,
        observation_status="observed",
    )
    defaults.update(kwargs)
    return HoldingResponse(**defaults)


def aggregate_by_security(holdings: list[HoldingResponse]) -> list[SecurityAggregateResponse]:
    """Mirror portfolio_service grouping rules without DB."""
    from collections import defaultdict

    groups: dict[str, list[HoldingResponse]] = defaultdict(list)
    for h in holdings:
        if h.isin:
            key = f"isin:{h.isin}"
        elif h.symbol and h.currency:
            key = f"symbol:{h.symbol}:{h.currency}"
        elif h.symbol:
            key = f"symbol:{h.symbol}:UNKNOWN"
        else:
            key = h.security_identifier
        groups[key].append(h)

    results = []
    for key, positions in sorted(groups.items()):
        currencies = {p.currency for p in positions if p.currency}
        currency = next(iter(currencies)) if len(currencies) == 1 else None
        qty = mv = None
        if len(currencies) <= 1:
            qtys = [p.quantity for p in positions if p.quantity is not None]
            mvs = [p.market_value for p in positions if p.market_value is not None]
            qty = sum(qtys, Decimal("0")) if qtys else None
            mv = sum(mvs, Decimal("0")) if mvs else None
        results.append(
            SecurityAggregateResponse(
                security_key=key,
                symbol=positions[0].symbol,
                security_name=positions[0].security_name,
                isin=positions[0].isin,
                currency=currency,
                total_quantity=qty,
                total_market_value=mv,
                positions=positions,
            )
        )
    return results


def test_duplicate_holdings_summed_per_broker_retained():
    positions = [
        _holding(id=1, connection_id=1, provider_id="afrinvestor", quantity=Decimal("1000")),
        _holding(id=2, connection_id=2, provider_id="broker_b", account_id=2, quantity=Decimal("2000")),
        _holding(id=3, connection_id=3, provider_id="broker_c", account_id=3, quantity=Decimal("500")),
    ]
    agg = aggregate_by_security(positions)
    assert len(agg) == 1
    assert agg[0].total_quantity == Decimal("3500")
    assert len(agg[0].positions) == 3


def test_do_not_merge_same_symbol_different_currency():
    positions = [
        _holding(id=1, currency="NGN", quantity=Decimal("100"), security_identifier="symbol:MTNN:NGN"),
        _holding(
            id=2,
            currency="USD",
            quantity=Decimal("50"),
            security_identifier="symbol:MTNN:USD",
            market_value=Decimal("10"),
        ),
    ]
    agg = aggregate_by_security(positions)
    assert len(agg) == 2


def test_isin_preferred_for_grouping():
    positions = [
        _holding(id=1, isin="NGMTNN000001", security_identifier="isin:NGMTNN000001", quantity=Decimal("10")),
        _holding(
            id=2,
            isin="NGMTNN000001",
            connection_id=2,
            account_id=2,
            security_identifier="isin:NGMTNN000001",
            quantity=Decimal("5"),
        ),
    ]
    agg = aggregate_by_security(positions)
    assert len(agg) == 1
    assert agg[0].security_key == "isin:NGMTNN000001"
    assert agg[0].total_quantity == Decimal("15")


def test_missing_price_not_treated_as_zero():
    h = _holding(current_price=None, market_value=None)
    assert h.current_price is None
    assert h.market_value is None
    # Aggregation of empty market values stays None
    agg = aggregate_by_security([h])
    assert agg[0].total_market_value is None


def test_currency_totals_not_mixed():
    from collections import defaultdict

    holdings = [
        _holding(currency="NGN", market_value=Decimal("100")),
        _holding(id=2, currency="USD", market_value=Decimal("50"), security_identifier="x"),
    ]
    by_ccy: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for h in holdings:
        if h.market_value is not None and h.currency:
            by_ccy[h.currency] += h.market_value
    assert by_ccy["NGN"] == Decimal("100")
    assert by_ccy["USD"] == Decimal("50")
    assert "TOTAL" not in by_ccy
