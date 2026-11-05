"""Observed Morgan Capital modern-app URLs (live session 2026-10-29).

Data is delivered via authenticated HTML/RSC pages (cookie ``mc_session``),
not REST ``/api/*`` (those 404). Paths below are the pages we scrape.
"""

from __future__ import annotations

MODERN_BASE = "https://www.morgancapitalgroup.com"

MODERN_SIGN_IN = f"{MODERN_BASE}/sign-in"
MODERN_DASHBOARD = f"{MODERN_BASE}/dashboard"
MODERN_PORTFOLIO = f"{MODERN_BASE}/portfolio"
MODERN_WALLET = f"{MODERN_BASE}/wallet"
MODERN_MARKETS = f"{MODERN_BASE}/markets"
MODERN_ORDERS = f"{MODERN_BASE}/orders"

# Next.js server action id for signIn (from sign-in page chunk)
SIGN_IN_ACTION_ID = "7007449683358a67772b2bea04983b29fdd55abe1f"
# Cloudflare Turnstile sitekey (explicit render)
TURNSTILE_SITEKEY = "0x4AAAAAAEkSMulb_IeE7kco"


def market_instrument(symbol: str) -> str:
    return f"{MODERN_BASE}/markets/{symbol.upper()}"
