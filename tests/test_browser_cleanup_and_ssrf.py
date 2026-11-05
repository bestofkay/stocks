"""Browser navigation restrictions and cleanup guarantees."""

from __future__ import annotations

import pytest

from app.connectors.afrinvestor.connector import AfrinvestConnector, _host_allowed
from app.connectors.afrinvestor import selectors


@pytest.mark.parametrize(
    "url,allowed",
    [
        ("https://afrinvestor.com/home", True),
        ("https://www.afrinvestor.com/portfolio", True),
        ("https://cdn.afrinvestor.com/asset.js", True),
        ("https://evil.com/", False),
        ("http://127.0.0.1/", False),
        ("http://169.254.169.254/latest/meta-data/", False),
        ("https://afrinvestor.com.attacker.test/", False),
        ("https://not-afrinvestor.com/", False),
    ],
)
def test_host_allowlist(url: str, allowed: bool):
    assert _host_allowed(url) is allowed


def test_approved_domains_frozen():
    assert "afrinvestor.com" in selectors.APPROVED_DOMAINS
    assert "example.com" not in selectors.APPROVED_DOMAINS


@pytest.mark.asyncio
async def test_disconnect_is_idempotent():
    connector = AfrinvestConnector()
    # Never started a browser — disconnect must still succeed
    await connector.disconnect()
    await connector.disconnect()
    assert connector._page is None
    assert connector._browser is None
    assert connector._authenticated is False


def test_integration_blockers_documented():
    assert len(selectors.INTEGRATION_BLOCKERS) >= 1
