"""Observed Afrinvestor authenticated API paths (same-origin, session cookie).

Discovered during authorized live integration against afrinvestor.com.
Browser login establishes the session; these endpoints supply portfolio and
write-side funding / equity order data.
"""

from __future__ import annotations

BASE = "https://afrinvestor.com"

PORTFOLIO_LIST = f"{BASE}/api/all/portfolio/list"
PORTFOLIO_NETS = f"{BASE}/api/all/portfolio/nets"
WALLET = f"{BASE}/api/all/wallet"
# POST with empty JSON body returns dedicated virtual-account funding details
WALLET_DEDICATED = f"{BASE}/api/all/wallet/dedicated"

EQUITIES_SEARCH = f"{BASE}/api/all/invest/equities"
# Observed default / only equity market code for this connector's account surface
DEFAULT_EQUITY_MARKET = "NGX"
EQUITY_BUY_REVIEW = f"{BASE}/api/all/invest/equities/buy/review"


def equities_list(
    *,
    market_id: str = DEFAULT_EQUITY_MARKET,
    search: str | None = None,
    page: int = 1,
    per_page: int = 100,
) -> str:
    from urllib.parse import urlencode

    params: dict[str, str | int] = {
        "marketId": market_id or DEFAULT_EQUITY_MARKET,
        "per_page": max(1, min(int(per_page), 100)),
        "page": max(1, int(page)),
    }
    if search and search.strip():
        params["search"] = search.strip()
    return f"{EQUITIES_SEARCH}?{urlencode(params)}"

EQUITY_BUY = f"{BASE}/api/all/invest/equities/buy"
EQUITY_SELL_REVIEW = f"{BASE}/api/all/invest/equities/sell/review"
EQUITY_SELL = f"{BASE}/api/all/invest/equities/sell"
EQUITY_ORDERS = f"{BASE}/api/all/invest/equities/orders"


def equity_holdings(portfolio_id: str) -> str:
    return f"{BASE}/api/all/portfolio/assets/{portfolio_id}/equity/holdings"


def equity_detail(sec_id: str, portfolio_id: str) -> str:
    return f"{BASE}/api/all/invest/equities/{sec_id}/{portfolio_id}"


def equity_orders(portfolio_id: str) -> str:
    return f"{EQUITY_ORDERS}?portfolioId={portfolio_id}"
