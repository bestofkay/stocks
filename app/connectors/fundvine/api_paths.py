"""Fundvine path constants — thin wrapper over shared NaYa OMS paths."""

from __future__ import annotations

from app.connectors.naya_oms.paths import COMMON_BLOCKERS, build_paths

_P = build_paths(
    "https://etrade.fundvinesecurities.com",
    extra_domains=frozenset({"offer.fundvinesecurities.com"}),
)

BASE = _P.base
APPROVED_DOMAINS = _P.approved_domains
LOGIN = _P.login
DEFAULT = _P.default
ASMX = _P.asmx
GET_CSCS_ACCOUNTS = _P.get_cscs_accounts
GET_STOCK_POSITIONS = _P.get_stock_positions
GET_DASHBOARD_SUMMARY = _P.get_dashboard_summary
GET_ACCOUNT_STATEMENTS = _P.get_account_statements
GET_SECURITIES = _P.get_securities
GET_ACCOUNT_ATTRIBUTES = _P.get_account_attributes
GET_PURCHASE_INFO = _P.get_purchase_info
GET_SALE_INFO = _P.get_sale_info
IS_MANDATE_VALID = _P.is_mandate_valid
GET_CUSTOM_APP_NAME = _P.get_custom_app_name
GET_NSE_TRADING_SESSION = _P.get_nse_trading_session
GET_NON_TRADE_CHARGES = _P.get_non_trade_charges
DEPOSIT_PAGE = _P.deposit_page
EQTY_MANDATE_BUY = _P.eqty_mandate_buy
EQTY_MANDATE_SELL = _P.eqty_mandate_sell
DEFAULT_MARKET = _P.default_market
INTEGRATION_BLOCKERS = list(COMMON_BLOCKERS)
ORDER_HISTORY_ASHX = f"{BASE}/Handlers/OrderHistoryJSON.ashx"
MY_ORDERS = f"{BASE}/Client/MyOrders.aspx"


def eqty_mandate(side: str) -> str:
    return _P.eqty_mandate(side)
