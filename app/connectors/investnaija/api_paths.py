"""Observed InvestNaija API surface (live session 2026-11-02).

Angular app https://app.investnaija.com calls ``https://invest-chd.zanibal.com/api/v1``.
Auth headers: ``Authorization: <JWT>`` (no "Bearer" prefix) + ``x-uuid-token: <uuid_token>``.
Portfolio detail / order calls additionally send ``x-secure-token: <portfolio.signature>``.
The login password is RSA-OAEP(SHA-256) encrypted client-side with the public key below
(embedded in the app bundle - a public key, not a secret).
"""

from __future__ import annotations

APP_ORIGIN = "https://app.investnaija.com"
API_BASE = "https://invest-chd.zanibal.com/api/v1"
APPROVED_DOMAINS: frozenset[str] = frozenset({"invest-chd.zanibal.com", "app.investnaija.com"})

PASSWORD_PUBLIC_KEY_B64 = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAu0QvKYLZhCxV7bcGmhjtxYWYQBtNWbJNeTYj/+F+XJ+a/A9//JoX7OqsSyWN1g2I"
    "GmzxrptomCI8p7F+SgoAzhF/UDuL8ncNf5DAgcy/gxj8EpSk4KUPz3SiVuct5n4g8c/w5Fi0Fbr4TC5pPBqbGyzsXwhfGZOnqcFn3qLKKDIJ"
    "/XytKtw3Wwla2qK0mg3V7DrPyvKstZbFPVEeJXSUYNlSt1waHFKhxm0qX3EWAurhhQWkBHbCzkpUyXkmoeknqZzE01sULF/ii2bqXsPj9FpI"
    "5Wg5JS+RVEIKuHKZE2+wLJugKfHnN8RxeWV+JO2ejXmV0mf3cvozNaEp3qS+kwIDAQAB"
)

LOGIN = f"{API_BASE}/auth/customers/login"  # POST {email, password(RSA), rememberMe}
REFRESH = f"{API_BASE}/auth/refresh"  # POST {}
PROFILE = f"{API_BASE}/customers/profile/fetch"
TRADE_PORTFOLIOS = f"{API_BASE}/trades/portfolios"
ASSET_PORTFOLIOS = f"{API_BASE}/investin/portfolios"
WALLET_BALANCE = f"{API_BASE}/transactions/wallet/balance"
SECURITIES = f"{API_BASE}/trades/securities"
ORDER_TERMS = f"{API_BASE}/trades/order-terms"
CALCULATE_PRICE = f"{API_BASE}/trades/trade-order/calculate-price"  # fee preview
CREATE_ORDER = f"{API_BASE}/trades/trade-order/create"  # + x-secure-token

DEFAULT_MARKET = "NGX"

INTEGRATION_BLOCKERS = [
    "No public API docs; endpoints observed from an authorized web session.",
    "Stock trading needs a TradeIN portfolio (GET /trades/portfolios); without one orders return 501.",
    "Asset-management (InvestIN) portfolios flagged 'unavailable' reject detail calls; list data is used.",
    "Savings/plans (SaveIN/PlanIN) and mutual-fund subscriptions are not imported yet.",
]


def trade_portfolio(pid: str) -> str:
    return f"{TRADE_PORTFOLIOS}/{pid}"


def asset_portfolio_balance(pid: str) -> str:
    return f"{API_BASE}/investin/portfolio-balance/{pid}"


def security_overview(symbol: str) -> str:
    return f"{API_BASE}/trades/security/overview/{symbol}"


def my_transactions(page: int = 1, size: int = 50) -> str:
    return f"{API_BASE}/transactions/my-transactions?page={page}&size={size}&channel=&source=&type=&start=&end="
