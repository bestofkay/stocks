"""Atlas Folio parser unit tests (no live credentials)."""

from __future__ import annotations

from decimal import Decimal

from app.connectors.atlas import parser


def test_parse_settings_from_wa_html():
    html = """
    <script>
      settings = {
        contextPath: "",
        appCoy: "Atlass Portfolios Limited",
        appName: "CitiTrader",
        user: "TEST USER",
        id: 25720,
        nonce: "abc",
        isTrader: true,
        allowCustomerMandates: true,
        tradeAccounts: [138050509],
        canPayOnline: false,
        email: "test@example.com"
      };
    </script>
    """
    s = parser.parse_settings_from_wa_html(html)
    assert s["id"] == 25720
    assert s["user"] == "TEST USER"
    assert s["tradeAccounts"] == [138050509]
    assert s["canPayOnline"] is False
    accts = parser.accounts_from_settings(s)
    assert len(accts) == 1
    assert accts[0].external_account_id == "138050509"


def test_cash_and_summary_from_dashboard():
    dash = {
        "acct": {"no_accts": 1, "bal": 5000, "avail_bal": 895.62, "cred_lmt": "0.00"},
        "pf": {"t": 4065, "d": [{"label": "Equity", "qty": 150, "value": 4065}]},
    }
    assert parser.cash_from_dashboard(dash) == Decimal("895.62")
    summary = parser.summary_from_dashboard(dash)
    assert summary is not None
    assert summary.total_market_value == Decimal("4065")
    holdings = parser.holdings_from_pf_summary(dash)
    assert len(holdings) == 1
    assert holdings[0].quantity == Decimal("150")


def test_holdings_from_portfolio_rows():
    rows = [
        {
            "smbl": "GTCO",
            "desc": "GTCO PLC",
            "qty": 100,
            "refPx": 119.5,
            "costPx": 100,
            "mktVal": 11950,
            "assetCost": 10000,
            "gainLoss": 19.5,
        }
    ]
    h = parser.holdings_from_portfolio_rows(rows)
    assert len(h) == 1
    assert h[0].symbol == "GTCO"
    assert h[0].market_value == Decimal("11950")


def test_instruments_from_mde():
    rows = [
        {"id": "GTCO", "rlstPx": 119.5, "brd": "EQTY", "exchng": "NGX"},
        {"id": "ZENITHBANK", "lstPx": "115.00", "brd": "PREMIUM"},
    ]
    result = parser.instruments_from_mde(rows, search="GTCO", page=1, per_page=10)
    assert result.total == 1
    assert result.instruments[0].symbol == "GTCO"
