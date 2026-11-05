"""Observed CardinalStone API surface (live session 2026-10-30).

The web app at https://app.cardinalstone.com is a React SPA that talks to a
JSON API at ``core2.cardinalstone.com`` with ``Authorization: Bearer <JWT>``.
Paths are base64-obfuscated in the JS bundle (``atob("...")``); everything
below was either called by the app in an authorized session or decoded from
that bundle and replayed. The API host is **not** behind the Cloudflare
challenge that fronts the SPA, so plain httpx works.
"""

from __future__ import annotations

from urllib.parse import urlencode

API_BASE = "https://core2.cardinalstone.com"
APP_ORIGIN = "https://app.cardinalstone.com"
# Market-data host used by the SPA for live security stats
STATS_BASE = "https://websocket.cardinalstone.com"

# Strict allow-list — the connector never sends a request elsewhere
APPROVED_DOMAINS: frozenset[str] = frozenset(
    {
        "core2.cardinalstone.com",
        "app.cardinalstone.com",
        "websocket.cardinalstone.com",
    }
)

# --- auth ---
LOGIN = f"{API_BASE}/auth/login"  # POST {username, password, deviceInfo}
REFRESH = f"{API_BASE}/auth/refresh"  # POST {} with Bearer <refresh_token>
VERIFY_2FA = f"{API_BASE}/auth/verify-2fa"  # POST {CustID, token}
PROFILE = f"{API_BASE}/users/profile"

# --- portfolio ---
MARKET_IS_OPEN = f"{API_BASE}/zanibal/market-is-open"
WALLET_BALANCE = f"{API_BASE}/wallet/wallet-balance"
NUBAN_ACCOUNTS = f"{API_BASE}/wallet/nuban-accounts"  # dedicated funding (virtual) account
NETWORTH = f"{API_BASE}/reporting/networth"

# --- trading ---
EQUITIES_PAGE_SIZE = 100  # server caps page size at 100
ORDERS_CALCULATE = f"{API_BASE}/zanibal/orders/calculate"  # preview units / value
VALIDATE_ORDER = f"{API_BASE}/zanibal/validate-order"  # step 1 (review)
ORDERS = f"{API_BASE}/zanibal/orders"  # step 2 (place) — same body as validate

DEFAULT_MARKET = "NGX"

INTEGRATION_BLOCKERS = [
    "No public API docs; endpoints observed from an authorized web session.",
    "Deposits land in the NGN Wallet (NUBAN); moving funds into the trading account is not automated.",
    "Order success payload shape inferred from the web app (account had no funds during discovery).",
    "Mutual funds / fixed income (neulogic endpoints) are not imported as holdings yet.",
]


def investment_accounts(client_id: str) -> str:
    return f"{API_BASE}/zanibal/investment-accounts/{client_id}"


def investment_account_details(account_id: str) -> str:
    return f"{API_BASE}/zanibal/investment-accounts/details/{account_id}"


def trading_account_report(account_id: str, value_date: str) -> str:
    return f"{API_BASE}/zanibal/trading-account-report/{account_id}/{value_date}"


def account_statement(account_id: str, start_date: str, end_date: str, *, page: int = 0, size: int = 200) -> str:
    q = urlencode({"startDate": start_date, "endDate": end_date, "page": page, "size": size})
    return f"{API_BASE}/zanibal/accountStatement/{account_id}?{q}"


def account_orders(account_id: str, *, page: int = 0, size: int = 100) -> str:
    return f"{API_BASE}/zanibal/orders/account/{account_id}?page={page}&size={size}"


def equities(page: int = 0, size: int = EQUITIES_PAGE_SIZE) -> str:
    size = max(1, min(int(size), EQUITIES_PAGE_SIZE))
    return f"{API_BASE}/zanibal/equities?order=desc&page={max(0, int(page))}&size={size}"


def cancel_order(order_id: str) -> str:
    return f"{API_BASE}/zanibal/cancel-orders/{order_id}"
