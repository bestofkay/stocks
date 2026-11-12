"""Shared NaYa OMS path builder tests."""

from app.connectors.naya_oms.paths import build_paths


def test_build_paths_iconstock():
    p = build_paths("https://etrade.iconstockbrokersltd.com")
    assert p.login.endswith("/Login.aspx")
    assert p.get_cscs_accounts.endswith("/GetCSCSAccounts")
    assert p.cookie_domain == "etrade.iconstockbrokersltd.com"
    assert "etrade.iconstockbrokersltd.com" in p.approved_domains
    assert p.eqty_mandate("sell").endswith("EqtyMandate.aspx?s=2")
    assert p.eqty_mandate("buy").endswith("EqtyMandate.aspx?s=1")


def test_build_paths_fundvine_extra_domain():
    p = build_paths(
        "https://etrade.fundvinesecurities.com",
        extra_domains=frozenset({"offer.fundvinesecurities.com"}),
    )
    assert "offer.fundvinesecurities.com" in p.approved_domains
