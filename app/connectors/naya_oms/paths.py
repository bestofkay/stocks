"""Path helpers for NaYa OMS etrade portals."""

from __future__ import annotations

from dataclasses import dataclass


COMMON_BLOCKERS = [
    "No public API docs; endpoints observed from an authorized NaYa OMS session.",
    "Funding is typically Paystack card via the portal (no dedicated NUBAN/VA observed).",
    "Equity order placement uses ASP.NET WebForms postback on EqtyMandate.aspx.",
    "Order-history ashx handlers often return HTML shell unless called like the SPA; statements cover activity.",
]


@dataclass(frozen=True)
class NaYaPaths:
    base: str
    approved_domains: frozenset[str]
    login: str
    default: str
    asmx: str
    get_cscs_accounts: str
    get_stock_positions: str
    get_dashboard_summary: str
    get_account_statements: str
    get_securities: str
    get_account_attributes: str
    get_purchase_info: str
    get_sale_info: str
    is_mandate_valid: str
    get_custom_app_name: str
    get_nse_trading_session: str
    get_non_trade_charges: str
    deposit_page: str
    eqty_mandate_buy: str
    eqty_mandate_sell: str
    cookie_domain: str
    default_market: str = "NGX"

    def eqty_mandate(self, side: str) -> str:
        s = str(side).strip().lower()
        if s in {"2", "sell", "s"}:
            return self.eqty_mandate_sell
        return self.eqty_mandate_buy


def build_paths(base: str, *, extra_domains: frozenset[str] | None = None) -> NaYaPaths:
    base = base.rstrip("/")
    host = base.split("://", 1)[-1].split("/", 1)[0].lower()
    domains = frozenset({host}) | (extra_domains or frozenset())
    asmx = f"{base}/Services/NaYaOMSServices.asmx"
    return NaYaPaths(
        base=base,
        approved_domains=domains,
        login=f"{base}/Login.aspx",
        default=f"{base}/Default.aspx",
        asmx=asmx,
        get_cscs_accounts=f"{asmx}/GetCSCSAccounts",
        get_stock_positions=f"{asmx}/GetStockPositions",
        get_dashboard_summary=f"{asmx}/GetDashBoardSummary",
        get_account_statements=f"{asmx}/GetAccountStatements",
        get_securities=f"{asmx}/GetSecurities",
        get_account_attributes=f"{asmx}/GetAccountAttributes",
        get_purchase_info=f"{asmx}/GetPurchaseInfo",
        get_sale_info=f"{asmx}/GetSaleInfo",
        is_mandate_valid=f"{asmx}/IsMandateValid",
        get_custom_app_name=f"{asmx}/GetCustomAppName",
        get_nse_trading_session=f"{asmx}/GetNseTradingSession",
        get_non_trade_charges=f"{asmx}/GetNonTradeCharges",
        deposit_page=f"{base}/Client/Deposit.aspx",
        eqty_mandate_buy=f"{base}/Client/EqtyMandate.aspx?s=1",
        eqty_mandate_sell=f"{base}/Client/EqtyMandate.aspx?s=2",
        cookie_domain=host,
    )
