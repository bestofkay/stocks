"""Atlas Portfolios (Caladata Folio / CitiTrader) observed surface — 2026-11-12."""

from __future__ import annotations

BASE = "https://app.atlassportfolios.com"

APPROVED_DOMAINS: frozenset[str] = frozenset({"app.atlassportfolios.com"})

CUSTOMERS = f"{BASE}/customers/"
LOGIN_PAGE = CUSTOMERS
AUTH_VALIDATE = f"{BASE}/folio/authCustomer?a=v"
AUTH_LOGIN = f"{BASE}/folio/authCustomer"
AUTH_OVERRIDE = f"{BASE}/folio/authCustomer?a=o"
LOGOUT_USER = f"{BASE}/customers/logoutUser?e=ft"
WA = f"{BASE}/customers/wa/"
WS = "wss://app.atlassportfolios.com/cftrader"

DEFAULT_MARKET = "NGX"

INTEGRATION_BLOCKERS = [
    "No public API docs; endpoints observed from an authorized CitiTrader session.",
    "Portfolio/order detail grids use Caladata Folio WebSocket services (cftrader).",
    "Online card funding (WebPay) is disabled for this portal (canPayOnline=false).",
    "Provider enforces single concurrent login; connector must logout to release the lock.",
]
