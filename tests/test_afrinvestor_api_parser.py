"""Unit tests for Afrinvestor authenticated API parsers (no live credentials)."""

from __future__ import annotations

from decimal import Decimal

from app.connectors.afrinvestor.parser import (
    accounts_from_portfolio_list,
    cash_from_wallet_api,
    holdings_from_equity_api,
    summary_from_nets_api,
)


HOLDINGS_FIXTURE = {
    "stats": {
        "total_cost": 2816.99,
        "total_market_value": 2775,
        "total_gain_loss": -41.99,
    },
    "holdings": [
        {
            "cost_basis": 28.17,
            "market_price": 27.75,
            "total_gain": -41.99,
            "market_value": 2775,
            "quantity_held": 100,
            "valuation": 2775,
            "value_date": "2026-10-22",
            "security_type": "EQUITY",
            "security_name": "ACCESSCORP",
            "security_label": "ACCESS HOLDINGS PLC",
            "sector": "Finance",
        }
    ],
}


def test_holdings_from_equity_api_maps_observed_fields():
    holdings = holdings_from_equity_api(HOLDINGS_FIXTURE)
    assert len(holdings) == 1
    h = holdings[0]
    assert h.symbol == "ACCESSCORP"
    assert h.security_name == "ACCESS HOLDINGS PLC"
    assert h.quantity == Decimal("100")
    assert h.current_price == Decimal("27.75")
    assert h.market_value == Decimal("2775")
    assert h.average_cost == Decimal("28.17")
    assert h.cost_basis == Decimal("2817.00")  # 28.17 * 100
    assert h.unrealized_pnl == Decimal("-41.99")
    assert h.currency == "NGN"
    assert h.valuation_timestamp is not None


def test_accounts_from_portfolio_list():
    accounts = accounts_from_portfolio_list(
        [
            {
                "id": "abc",
                "name": "0000591003",
                "label": "Soji Okunuga",
                "type": "SELF",
            }
        ]
    )
    assert len(accounts) == 1
    assert accounts[0].external_account_id == "abc"
    assert accounts[0].account_name == "Soji Okunuga"
    assert accounts[0].currency == "NGN"


def test_cash_and_summary():
    assert cash_from_wallet_api({"total_balance": 3134.63, "currency": "NGN"}) == Decimal(
        "3134.63"
    )
    summary = summary_from_nets_api(
        {"available_cash": 3134.63, "total_valuation": 5909.63, "total_equity_valuation": 2775}
    )
    assert summary is not None
    assert summary["cash_value"] == Decimal("3134.63")
    assert summary["total_market_value"] == Decimal("5909.63")
