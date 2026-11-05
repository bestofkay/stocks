"""Cordros Capital (CAPE) authenticated-HTTP connector.

Implementation status: SCAFFOLDED (endpoints live-observed 2026-11-02)
---------------------------------------------------------------------
Login (POST auth/login), profile, market data and deposit instructions were
replayed live. Portfolio / cash / trades / orders use SymPlus gateway
operations read from the web app code; the discovery account has no customer
record yet (KYC pending), so those return ``data: null`` and are unverified
with real positions. Flip to OPERATIONAL after scripts/record_cordros_flow.py passes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import httpx

from app.connectors.base import BrokerConnector
from app.connectors.models import (
    AuthMethod,
    AuthResult,
    ConnectorType,
    CredentialField,
    DataCapability,
    DepositInstructions,
    ImplementationStatus,
    InstrumentListResult,
    MarketInfo,
    NormalizedAccount,
    NormalizedHolding,
    NormalizedTransaction,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderType,
    PortfolioSummary,
    ProviderMetadata,
)
from app.connectors.registry import register_connector
from app.core.errors import AppError, ErrorCode

from . import api_paths, parser

logger = logging.getLogger(__name__)
WAT = timezone(timedelta(hours=1))
SESSION_KEY = "api_session"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def _host_allowed(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in api_paths.APPROVED_DOMAINS


@register_connector("cordros")
class CordrosConnector(BrokerConnector):
    provider_id = "cordros"
    provider_name = "Cordros Capital"
    transport: httpx.AsyncBaseTransport | None = None  # tests inject MockTransport

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._token: str | None = None
        self._customer: dict[str, Any] | None = None
        self._authenticated = False
        self._account_meta: dict[str, dict[str, Any]] = {}
        self._prices: list[dict[str, Any]] | None = None

    @classmethod
    def metadata(cls) -> ProviderMetadata:
        return ProviderMetadata(
            provider_id="cordros",
            provider_name="Cordros Capital",
            connector_type=ConnectorType.AUTHENTICATED_HTTP,
            implementation_status=ImplementationStatus.SCAFFOLDED,
            auth_methods=[AuthMethod.USERNAME_PASSWORD],
            required_credential_fields=[
                CredentialField(name="username", label="Email or username", field_type="string", required=True,
                                secret=False, description="Cordros CAPE (app.cordros.com) login"),
                CredentialField(name="password", label="Password", field_type="password", required=True, secret=True),
            ],
            mfa_supported=False,
            capabilities=[
                DataCapability.ACCOUNTS,
                DataCapability.HOLDINGS,
                DataCapability.CASH_BALANCE,
                DataCapability.TRANSACTIONS,
                DataCapability.PORTFOLIO_SUMMARY,
                DataCapability.DEPOSIT_INSTRUCTIONS,
                DataCapability.ORDERS,
                DataCapability.INSTRUMENTS,
            ],
            approved_domains=sorted(api_paths.APPROVED_DOMAINS),
            notes=(
                "Authenticated HTTP against unified.cordros.com/api/v1 (Bearer JWT, 6 h). Stock data and "
                "orders via the SymPlus gateway (general-stock-api). Caveats: "
                + "; ".join(api_paths.INTEGRATION_BLOCKERS)
            ),
            operational=False,
        )

    # ------------------------------------------------------------------ auth

    async def authenticate(self, credentials: dict[str, Any], options: dict[str, Any] | None = None) -> AuthResult:
        options = options or {}
        email = (credentials.get("username") or credentials.get("email") or "").strip().lower()  # web app lower-cases
        password = credentials.get("password") or ""
        try:
            session = options.get(SESSION_KEY)
            if isinstance(session, dict) and session.get("token"):
                if await self._resume(session["token"]):
                    return self._ok("Authenticated via stored API session", "session")
            if not email or not password:
                return AuthResult(success=False, status="failed", message="Email/username and password are required")
            resp = await self._raw("POST", api_paths.LOGIN, json={"email": email, "password": password})
            body = self._json(resp)
            if resp.status_code >= 500:
                raise AppError(ErrorCode.PROVIDER_UNAVAILABLE, "Cordros login service unavailable", status_code=503)
            info = parser.login_info(body)
            if resp.status_code >= 400 or not info["access_token"]:
                return AuthResult(success=False, status="failed",
                                  message=parser.message(body) or "Invalid Customer Login Credential")
            self._token = info["access_token"]
            self._customer = parser.customer_from_user(info["user"])
            return self._ok("Authenticated successfully", "password")
        except AppError:
            raise
        except httpx.TimeoutException as exc:
            await self.disconnect()
            raise AppError(ErrorCode.AUTHENTICATION_TIMEOUT, "Cordros authentication timed out", status_code=503) from exc
        except Exception as exc:
            logger.exception("Cordros authenticate failed")
            await self.disconnect()
            raise AppError(ErrorCode.PROVIDER_UNAVAILABLE,
                           "Authentication attempt failed due to a provider or network error", status_code=503) from exc

    async def _resume(self, token: str) -> bool:
        self._token = token
        try:
            resp = await self._raw("GET", api_paths.PROFILE, auth=True)
            if resp.status_code == 401 and await self._refresh():
                resp = await self._raw("GET", api_paths.PROFILE, auth=True)
            if resp.status_code >= 400:
                return False
            self._customer = parser.customer_from_user(parser.user_from_profile(self._json(resp)))
            return True
        except httpx.HTTPError:
            return False

    async def _refresh(self) -> bool:
        if not self._token:
            return False
        resp = await self._raw("POST", api_paths.REFRESH, json={}, auth=True)
        if resp.status_code >= 400:
            return False
        info = parser.login_info(self._json(resp))
        if not info["access_token"]:
            return False
        self._token = info["access_token"]
        if info["user"]:
            self._customer = parser.customer_from_user(info["user"]) or self._customer
        return True

    def _ok(self, msg: str, mode: str) -> AuthResult:
        self._authenticated = True
        return AuthResult(
            success=True, status="authenticated", message=msg,
            session_metadata={"auth_mode": mode, "has_customer_record": self._customer is not None},
            options_patch={SESSION_KEY: {"token": self._token, "saved_at": datetime.now(timezone.utc).isoformat()}},
        )

    # -------------------------------------------------------------- portfolio

    async def get_accounts(self) -> list[NormalizedAccount]:
        self._require_auth()
        if not self._customer or not self._customer.get("st_id"):
            logger.info("Cordros: no active customer record (KYC pending) - no trading accounts yet")
            return []
        rows = await self._gateway(api_paths.op_market_accounts(self._customer["st_id"]))
        pairs = parser.market_accounts(rows)
        self._account_meta = {a.external_account_id: m for a, m in pairs}
        out = []
        for acct, _ in pairs:
            acct.cash_balance = parser.cash_balance(await self._gateway(api_paths.op_cash_balance(acct.external_account_id)))
            out.append(acct)
        return out

    async def get_holdings(self, account: NormalizedAccount) -> list[NormalizedHolding]:
        self._require_customer()
        return parser.holdings_from_portfolio(await self._gateway(api_paths.op_portfolio(self._customer["cscs"])))

    async def get_cash_balance(self, account: NormalizedAccount) -> Decimal | None:
        self._require_customer()
        return parser.cash_balance(await self._gateway(api_paths.op_cash_balance(account.external_account_id)))

    async def get_portfolio_summary(self, account: NormalizedAccount) -> PortfolioSummary | None:
        self._require_customer()
        rows = await self._gateway(api_paths.op_portfolio(self._customer["cscs"]))
        return parser.summary(rows, cash=await self.get_cash_balance(account))

    async def get_transactions(self, account: NormalizedAccount) -> list[NormalizedTransaction]:
        self._require_customer()
        end = datetime.now(WAT).date()
        start = end - timedelta(days=365)
        rows = await self._gateway(api_paths.op_trades(account.external_account_id, start.isoformat(), end.isoformat()))
        return parser.trades(rows, account=account.external_account_id)

    # --------------------------------------------------------------- deposits

    async def get_deposit_instructions(self) -> DepositInstructions:
        self._require_auth()
        payload = await self._request_json("GET", api_paths.PORTFOLIO_CONFIG)
        try:
            return parser.deposit_from_config(payload)
        except LookupError as exc:
            raise AppError(ErrorCode.CONNECTOR_NOT_IMPLEMENTED, "Cordros returned no payment instructions",
                           status_code=501) from exc
        except ValueError as exc:
            raise AppError(ErrorCode.PORTFOLIO_EXTRACTION_FAILED, f"Could not parse Cordros payment instructions: {exc}",
                           status_code=502) from exc

    # ---------------------------------------------------------- instruments

    async def list_markets(self) -> list[MarketInfo]:
        self._require_auth()
        return [MarketInfo(market_id=api_paths.DEFAULT_MARKET, name="Nigerian Exchange (NGX)", currency="NGN")]

    async def list_instruments(self, *, market_id: str | None = None, search: str | None = None,
                               page: int = 1, per_page: int = 100) -> InstrumentListResult:
        self._require_auth()
        if market_id and market_id.strip().upper() not in {"NGX", "XNSA"}:
            return InstrumentListResult(market_id=market_id, page=page, per_page=per_page, total=0, instruments=[])
        if self._prices is None:
            self._prices = await self._gateway(api_paths.OP_PRICES) or []
        return parser.instruments(self._prices, search=search, page=page, per_page=per_page)

    # ----------------------------------------------------------------- orders

    async def place_order(self, order: OrderRequest) -> OrderResult:
        self._require_auth()
        if not self._customer or not self._customer.get("st_id"):
            raise AppError(ErrorCode.CONNECTOR_NOT_IMPLEMENTED,
                           "Cordros trading needs an approved customer record (complete KYC on app.cordros.com)",
                           status_code=501)
        symbol = (order.symbol or "").strip().upper()
        if not symbol:
            raise AppError(ErrorCode.VALIDATION_ERROR, "symbol is required", status_code=422)
        if order.order_type == OrderType.LIMIT and (order.limit_price is None or order.limit_price <= 0):
            raise AppError(ErrorCode.VALIDATION_ERROR, "limit_price is required for limit orders", status_code=422)

        if not self._account_meta:
            await self.get_accounts()
        wanted = (order.account_external_id or "").strip()
        if wanted and wanted not in self._account_meta:
            raise AppError(ErrorCode.ACCOUNT_NOT_FOUND, "Market account not found on this Cordros connection", status_code=404)
        acct_id = wanted or next(iter(self._account_meta), None)
        if not acct_id:
            raise AppError(ErrorCode.ACCOUNT_NOT_FOUND, "No Cordros market account found", status_code=404)
        market_id = self._account_meta[acct_id].get("market_id")

        if order.quantity is not None and order.quantity > 0:
            if order.quantity != order.quantity.to_integral_value():
                raise AppError(ErrorCode.VALIDATION_ERROR, "Fractional shares are not allowed", status_code=422)
            qty = int(order.quantity)
        elif order.amount is not None and order.amount > 0:
            price = order.limit_price if order.order_type == OrderType.LIMIT else parser.price_from_rows(
                await self._gateway(api_paths.op_price(symbol)))
            if not price:
                raise AppError(ErrorCode.PROVIDER_UNAVAILABLE, f"Could not resolve a price for {symbol}", status_code=502)
            qty = parser.units_for_amount(order.amount, price)
            if qty <= 0:
                raise AppError(ErrorCode.VALIDATION_ERROR, "amount is below the price of one share", status_code=422)
        else:
            raise AppError(ErrorCode.VALIDATION_ERROR, "quantity (shares) or amount (NGN) must be positive", status_code=422)

        tif = (order.tif or "DAY").strip().upper()
        tif = api_paths.TIF_GTD if tif in {"GTD", "GOOD_TILL_DATE"} else tif
        if tif not in {api_paths.TIF_DAY, api_paths.TIF_GTD}:
            raise AppError(ErrorCode.VALIDATION_ERROR, "tif must be DAY or GTD", status_code=422)
        if tif == api_paths.TIF_GTD and not order.expires:
            raise AppError(ErrorCode.VALIDATION_ERROR, "expires (YYYY-MM-DD) is required for GTD", status_code=422)

        line = {
            "market_id": market_id,
            "market_accountid": acct_id,
            "symbol_code": symbol,
            "order_quantity": str(qty),
            "order_type": api_paths.ORDER_TYPE_LIMIT if order.order_type == OrderType.LIMIT else api_paths.ORDER_TYPE_MARKET,
            "limit_price": format(order.limit_price, "f") if order.order_type == OrderType.LIMIT else "",
            "time_in_force": tif,
            "expiry_date": order.expires if tif == api_paths.TIF_GTD else "",
        }
        is_buy = order.side == OrderSide.BUY
        payload = await self._request_json("POST", api_paths.STOCK_GATEWAY, {
            "endpoint": api_paths.OP_BUY if is_buy else api_paths.OP_SELL,
            "platform": api_paths.PLATFORM_ST,
            "payload": {("buyorder" if is_buy else "sellorder"): [line]},
        })
        if not (isinstance(payload, dict) and payload.get("success") is True):
            raise AppError(ErrorCode.VALIDATION_ERROR, parser.message(payload) or "Cordros rejected the order", status_code=422)
        return parser.order_result(payload, side=order.side.value, symbol=symbol)

    # -------------------------------------------------------------- lifecycle

    async def test_connection(self) -> AuthResult:
        try:
            resp = await self._raw("GET", api_paths.APP_ORIGIN + "/login")
            ok = resp.status_code < 500
            return AuthResult(success=ok, status="authenticated" if ok else "failed",
                              message="Cordros reachable" if ok else f"Cordros returned HTTP {resp.status_code}")
        except Exception:
            logger.exception("Cordros test_connection failed")
            return AuthResult(success=False, status="failed", message="Unable to reach Cordros")
        finally:
            await self.disconnect()

    async def disconnect(self) -> None:
        self._authenticated = False
        self._token = None
        self._account_meta = {}
        self._prices = None
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                logger.exception("Error closing Cordros HTTP client (ignored)")
            self._client = None

    # -------------------------------------------------------------- internals

    def _require_auth(self) -> None:
        if not self._authenticated or not self._token:
            raise AppError(ErrorCode.INVALID_CREDENTIALS, "Not authenticated with Cordros", status_code=401)

    def _require_customer(self) -> None:
        self._require_auth()
        if not self._customer or not self._customer.get("cscs"):
            raise AppError(ErrorCode.ACCOUNT_NOT_FOUND,
                           "No Cordros customer record yet (complete KYC on app.cordros.com)", status_code=404)

    async def _raw(self, method: str, url: str, *, json: Any = None, auth: bool = False) -> httpx.Response:
        if not _host_allowed(url):
            raise AppError(ErrorCode.NAVIGATION_BLOCKED, "API request blocked: host not in provider allow-list", status_code=400)
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=False, transport=self.transport,
                                             headers={"Accept": "application/json", "User-Agent": _UA,
                                                      "Origin": api_paths.APP_ORIGIN, "Referer": api_paths.APP_ORIGIN + "/"})
        headers = {"Authorization": f"Bearer {self._token}"} if auth and self._token else None
        return await self._client.request(method, url, json=json, headers=headers)

    @staticmethod
    def _json(resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except ValueError:
            return None

    async def _request_json(self, method: str, url: str, body: dict[str, Any] | None = None) -> Any:
        resp = await self._raw(method, url, json=body, auth=True)
        if resp.status_code == 401 and await self._refresh():
            resp = await self._raw(method, url, json=body, auth=True)
        payload = self._json(resp)
        if resp.status_code == 401:
            self._authenticated = False
            raise AppError(ErrorCode.INVALID_CREDENTIALS, "Cordros session expired; re-authenticate", status_code=401)
        if resp.status_code >= 500:
            raise AppError(ErrorCode.PROVIDER_UNAVAILABLE,
                           parser.message(payload) or f"Cordros API returned HTTP {resp.status_code}", status_code=502)
        if resp.status_code >= 400:
            raise AppError(ErrorCode.VALIDATION_ERROR,
                           parser.message(payload) or f"Cordros API returned HTTP {resp.status_code}", status_code=422)
        return payload

    async def _gateway(self, operation: str) -> list[dict[str, Any]] | None:
        payload = await self._request_json("POST", api_paths.STOCK_GATEWAY,
                                           {"endpoint": operation, "platform": api_paths.PLATFORM_ST})
        if isinstance(payload, dict) and payload.get("success") is False:
            raise AppError(ErrorCode.PORTFOLIO_EXTRACTION_FAILED,
                           parser.message(payload) or "Cordros gateway error", status_code=502)
        return parser.gateway_rows(payload, operation)
