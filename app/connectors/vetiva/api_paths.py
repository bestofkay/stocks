"""Observed Vetiva API surface (live session 2026-10-31).

The client portal (https://client.vetiva.com, Next.js) calls a same-origin
proxy at ``/api/proxy/*`` with ``Authorization: Bearer <JWT>``. The proxy
accepts the bearer header alone (no cookies needed) and the host is not behind
a Cloudflare challenge, so plain httpx works. Paths below were called by the
app in an authorized session or read from its RTK Query definitions and replayed.
"""

from __future__ import annotations

from urllib.parse import urlencode

APP_ORIGIN = "https://client.vetiva.com"
API_BASE = f"{APP_ORIGIN}/api/proxy"

APPROVED_DOMAINS: frozenset[str] = frozenset({"client.vetiva.com"})

# --- auth ---
LOGIN = f"{API_BASE}/auth/login"  # POST {email, password, channel:"web"}
VERIFY_2FA = f"{API_BASE}/auth/verify-2fa"  # POST {channel, email, password, token} (emailed 6-digit code)
REFRESH = f"{API_BASE}/auth/refresh"  # POST {refresh_token}
PROFILE = f"{API_BASE}/account/individual?channel=web"
LOGIN_PAGE = f"{APP_ORIGIN}/v2"

# --- portfolio / cash ---
PORTFOLIO_VALUE = f"{API_BASE}/portfolio/value"
PORTFOLIO_BREAKDOWN = f"{API_BASE}/portfolio/breakdown-expanded"
WALLETS = f"{API_BASE}/payment/get-account-wallets"

# --- trading (VSL) ---
SECURITIES_PAGE_SIZE = 500
ORDER = f"{API_BASE}/vsl/order"  # POST + Idempotency-Key header
ORDER_FEE_PREVIEW = f"{API_BASE}/vsl/order/fee-preview"  # POST, same body as ORDER
MARKET_KEY = "NG"
DEFAULT_MARKET = "NGX"

# Order codes used by the portal (FIX-style, all strings)
ORDER_TYPE_MARKET = "1"
ORDER_TYPE_LIMIT = "2"
SIDE_BUY = "1"
SIDE_SELL = "2"
TIF_DAY = "0"
TIF_GTD = "6"

INTEGRATION_BLOCKERS = [
    "No public API docs; endpoints observed from an authorized web session.",
    "Vetiva allows one active session per user: a connector login signs the web portal out (and vice versa).",
    "Trading requires a VSL trade account, opened after KYC; until then orders/fee preview return 'You Do Not Have A Trade Account Yet'.",
    "No dedicated virtual account: funding is a hosted card/transfer checkout (initiate-payment), so deposit instructions are not offered.",
    "Holding rows expose value, units and unrealized P&L only; price and cost are derived from them.",
]


def portfolio_assets(page: int = 1, page_size: int = 100) -> str:
    return f"{API_BASE}/portfolio/assets?{urlencode({'page': page, 'page_size': page_size})}"


def wallet_transactions(page: int = 1, size: int = 100, date_from: str | None = None, date_to: str | None = None) -> str:
    q: dict[str, str | int] = {"page": page, "size": size}
    if date_from:
        q["date_from"] = date_from
    if date_to:
        q["date_to"] = date_to
    return f"{API_BASE}/payment/get-wallet-transactions?{urlencode(q)}"


def trading_transactions(page: int = 1, page_size: int = 100, date_from: str | None = None, date_to: str | None = None) -> str:
    q: dict[str, str | int] = {"page": page, "page_size": page_size}
    if date_from:
        q["date_from"] = date_from
    if date_to:
        q["date_to"] = date_to
    return f"{API_BASE}/vsl/trading-account/transactions?{urlencode(q)}"


def list_securities(page: int = 1, size: int = SECURITIES_PAGE_SIZE, name: str | None = None) -> str:
    q: dict[str, str | int] = {"page": page, "size": size}
    if name:
        q["name"] = name
    return f"{API_BASE}/vsl/list-securities?{urlencode(q)}"


def security_stats(symbol: str) -> str:
    return f"{API_BASE}/vsl/get-sec-stats?{urlencode({'symbol': symbol})}"


def market_status() -> str:
    return f"{API_BASE}/vsl/market-status?market_key={MARKET_KEY}"
