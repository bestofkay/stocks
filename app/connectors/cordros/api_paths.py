"""Observed Cordros CAPE API surface (live session 2026-11-02).

Web app https://app.cordros.com (jQuery + axios) calls
``https://unified.cordros.com/api/v1/`` with ``Authorization: Bearer <JWT>``.
Stock data and orders go through one gateway, ``POST general-stock-api``
``{endpoint: "<Operation>/<args>", platform: "ST", payload?}``, backed by
Cordros' SymPlus system. The login page runs reCAPTCHA v3 but the token is
NOT sent with the login request, so plain httpx login works without touching it.
"""

from __future__ import annotations

API_BASE = "https://unified.cordros.com/api/v1/"
APP_ORIGIN = "https://app.cordros.com"
APPROVED_DOMAINS: frozenset[str] = frozenset({"unified.cordros.com", "app.cordros.com"})

LOGIN = API_BASE + "auth/login"  # POST {email, password} -> {access_token, user{customer{...}}}
REFRESH = API_BASE + "auth/refresh"  # POST (Bearer) -> {access_token, user}
PROFILE = API_BASE + "auth/user-profile"  # GET -> {success, data: user}
STOCK_GATEWAY = API_BASE + "general-stock-api"  # POST {endpoint, platform:"ST", payload?}
PORTFOLIO_CONFIG = API_BASE + "securities/portfolio-configuration"  # GET -> data.payment_info (HTML)

PLATFORM_ST = "ST"
DEFAULT_MARKET = "NGX"
NGX_MARKET_CODES = ("XNSA-EQTY", "XNSA-PREMIUM", "XNSA-GROWTH", "XNSA-ETF", "XNSA-ASEM", "XNSA-NONINTEREST")

# Order form codes (trading page)
ORDER_TYPE_MARKET = "M"
ORDER_TYPE_LIMIT = "L"
TIF_DAY = "DAY"
TIF_GTD = "GTD"

INTEGRATION_BLOCKERS = [
    "No public API docs; endpoints observed from an authorized web session.",
    "Portfolio, cash, transactions and orders need a Cordros customer record (issued after KYC); "
    "until then the connector returns no accounts and orders return 501.",
    "Deposit instructions are the shared Cordros Securities collection account (not a dedicated VA); "
    "the 'I have paid' notification (payments/st-cash-deposit) is not automated.",
    "Orders are sent directly (DoBuyOrder/DoSellOrder) — the web app has no separate review step.",
]


def customer_transactions(customer_id: str) -> str:
    return f"{API_BASE}customer-transactions/{customer_id}"


# --- gateway operations ---
def op_portfolio(cscs: str) -> str:
    return f"GetStockPortfolio/{cscs}"


def op_market_accounts(st_id: str) -> str:
    return f"GetMarketAccounts/{st_id}"


def op_cash_balance(market_account_id: str) -> str:
    return f"/GetCashBalance/{market_account_id}"


def op_trades(market_account_id: str, start: str, end: str) -> str:
    return f"GetTradeByPostDate/{market_account_id}/{start}/{end}"


def op_executed_orders(cscs: str) -> str:
    return f"GetExecutedOrders/{cscs}"


def op_open_orders(cscs: str) -> str:
    return f"GetMarketOpenOrders/{cscs}"


OP_PRICES = "GetMarketStockPrices"
OP_MARKET_STATUS = "GetMarketStatusByID/XNSA-EQTY"


def op_price(symbol: str) -> str:
    return f"GetMarketStockPrice/{symbol}"


OP_BUY = "/DoBuyOrder/"
OP_SELL = "/DoSellOrder/"
