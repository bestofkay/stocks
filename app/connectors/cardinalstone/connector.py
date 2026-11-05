"""CardinalStone authenticated-HTTP connector.

Implementation status: OPERATIONAL (live-verified 2026-11-04)
-------------------------------------------------------------
Login, portfolio, wallet, statement, NUBAN funding, equities and order
review/placement endpoints observed at https://app.cardinalstone.com and
verified via ``scripts/record_cardinalstone_flow.py`` / API flow.

Session handling: the bearer + refresh tokens are returned as
``AuthResult.options_patch["api_session"]`` and stored **encrypted** in the
connection options, so most syncs skip the password login (and 2FA).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
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

# Nigerian market dates follow West Africa Time (UTC+1, no DST)
WAT = timezone(timedelta(hours=1))
SESSION_KEY = "api_session"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def _host_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return bool(host) and host in api_paths.APPROVED_DOMAINS


def _today() -> str:
    return datetime.now(WAT).date().isoformat()


class _Unauthorized(Exception):
    pass


@register_connector("cardinalstone")
class CardinalStoneConnector(BrokerConnector):
    provider_id = "cardinalstone"
    provider_name = "CardinalStone"

    # Tests inject an httpx.MockTransport here
    transport: httpx.AsyncBaseTransport | None = None

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._token: str | None = None
        self._refresh_token: str | None = None
        self._cust_id: str | None = None
        self._client_id: str | None = None
        self._authenticated = False
        self._pending_2fa = False
        self._account_meta: dict[str, dict[str, Any]] = {}
        self._equities: list[dict[str, Any]] | None = None

    @classmethod
    def metadata(cls) -> ProviderMetadata:
        return ProviderMetadata(
            provider_id="cardinalstone",
            provider_name="CardinalStone",
            connector_type=ConnectorType.AUTHENTICATED_HTTP,
            implementation_status=ImplementationStatus.OPERATIONAL,
            auth_methods=[AuthMethod.USERNAME_PASSWORD],
            required_credential_fields=[
                CredentialField(
                    name="username",
                    label="CustID, username or email",
                    field_type="string",
                    required=True,
                    secret=False,
                    description="Same identifier used on app.cardinalstone.com",
                ),
                CredentialField(
                    name="password",
                    label="Password",
                    field_type="password",
                    required=True,
                    secret=True,
                ),
            ],
            optional_credential_fields=[
                CredentialField(
                    name="otp",
                    label="Authenticator (2FA) code",
                    field_type="otp",
                    required=False,
                    secret=True,
                    description="6-digit code; only when 2FA is enabled and status is mfa_required",
                ),
            ],
            mfa_supported=True,
            capabilities=[
                DataCapability.ACCOUNTS,
                DataCapability.HOLDINGS,
                DataCapability.CASH_BALANCE,
                DataCapability.TRANSACTIONS,
                DataCapability.PORTFOLIO_SUMMARY,
                DataCapability.MFA,
                DataCapability.DEPOSIT_INSTRUCTIONS,
                DataCapability.ORDERS,
                DataCapability.INSTRUMENTS,
            ],
            approved_domains=sorted(api_paths.APPROVED_DOMAINS),
            notes=(
                "Authenticated HTTP against core2.cardinalstone.com (Bearer JWT, refresh "
                "token stored encrypted). NGX trading account holdings, cash, statement, "
                "NGN Wallet + NUBAN funding details, NGX equities, equity buy/sell "
                "(validate then place). Caveats: " + "; ".join(api_paths.INTEGRATION_BLOCKERS)
            ),
            operational=True,
        )

    # ------------------------------------------------------------------ auth

    async def authenticate(
        self,
        credentials: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> AuthResult:
        options = options or {}
        username = (credentials.get("username") or credentials.get("email") or "").strip()
        password = credentials.get("password") or ""
        otp = (credentials.get("otp") or "").strip()

        try:
            # 1) Reuse encrypted session tokens (skips password + 2FA)
            session = options.get(SESSION_KEY)
            if isinstance(session, dict) and session.get("token") and not otp:
                if await self._resume_session(session):
                    return self._auth_success("Authenticated via stored API session", "session")

            if not username or not password:
                return AuthResult(success=False, status="failed", message="Username and password are required")

            # 2) Password login
            await self._ensure_client()
            resp = await self._raw("POST", api_paths.LOGIN, json={
                "username": username,
                "password": password,
                "deviceInfo": {
                    "deviceName": "Portfolio Aggregator",
                    "deviceModel": None,
                    "deviceSerial": None,
                    "osVersion": None,
                    "manufacturer": None,
                    "ipAddress": None,
                    "latitude": None,
                    "longitude": None,
                    "geolocation": None,
                    "isp": None,
                    "connectionStatus": "online",
                },
            })
            body = self._json(resp)
            if resp.status_code >= 500:
                raise AppError(ErrorCode.PROVIDER_UNAVAILABLE, "CardinalStone login service unavailable", status_code=503)
            info = parser.login_tokens(body)
            if info["maintenance"]:
                return AuthResult(success=False, status="failed", message="CardinalStone is in maintenance mode; retry later")
            if resp.status_code >= 400 or not info["token"]:
                return AuthResult(
                    success=False,
                    status="failed",
                    message=parser.broker_message(body) or "Authentication failed. Check credentials.",
                )

            self._token = info["token"]
            self._refresh_token = info["refresh_token"]
            self._cust_id = info["cust_id"]
            self._client_id = info["client_id"]

            if info["is_2fa_enabled"]:
                if otp:
                    return await self.submit_mfa(otp)
                self._pending_2fa = True
                return AuthResult(
                    success=False,
                    status="mfa_required",
                    message="2FA enabled. Submit the authenticator code via POST /connections/{id}/mfa then re-sync.",
                    mfa_challenge={"type": "totp", "provider": "cardinalstone", "digits": 6},
                )

            await self._ensure_client_id()
            return self._auth_success("Authenticated successfully", "password")
        except AppError:
            raise
        except httpx.TimeoutException as exc:
            await self.disconnect()
            raise AppError(ErrorCode.AUTHENTICATION_TIMEOUT, "CardinalStone authentication timed out", status_code=503) from exc
        except Exception as exc:
            logger.exception("CardinalStone authenticate failed")
            await self.disconnect()
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Authentication attempt failed due to a provider or network error",
                status_code=503,
            ) from exc

    async def submit_mfa(self, code: str, challenge: dict[str, Any] | None = None) -> AuthResult:
        if not self._token or not self._cust_id:
            return AuthResult(success=False, status="failed", message="No pending CardinalStone login for 2FA")
        code = (code or "").strip()
        if len(code) != 6 or not code.isdigit():
            return AuthResult(success=False, status="mfa_required", message="2FA code must be 6 digits")
        resp = await self._raw("POST", api_paths.VERIFY_2FA, json={"CustID": self._cust_id, "token": code}, auth=True)
        if resp.status_code >= 400:
            return AuthResult(
                success=False,
                status="mfa_required",
                message=parser.broker_message(self._json(resp)) or "2FA code rejected",
            )
        self._pending_2fa = False
        await self._ensure_client_id()
        return self._auth_success("2FA accepted", "password+2fa")

    async def _resume_session(self, session: dict[str, Any]) -> bool:
        await self._ensure_client()
        self._token = session.get("token")
        self._refresh_token = session.get("refresh_token")
        self._cust_id = session.get("cust_id")
        self._client_id = session.get("client_id")
        try:
            resp = await self._raw("GET", api_paths.PROFILE, auth=True)
            if resp.status_code == 401 and await self._refresh():
                resp = await self._raw("GET", api_paths.PROFILE, auth=True)
            if resp.status_code >= 400:
                return False
            ids = parser.client_ids_from_profile(self._json(resp))
            self._cust_id = ids["cust_id"] or self._cust_id
            self._client_id = ids["client_id"] or self._client_id
            return bool(self._client_id)
        except httpx.HTTPError:
            return False

    async def _refresh(self) -> bool:
        if not self._refresh_token:
            return False
        resp = await self._raw(
            "POST", api_paths.REFRESH, json={}, headers={"Authorization": f"Bearer {self._refresh_token}"}
        )
        if resp.status_code >= 400:
            return False
        info = parser.login_tokens(self._json(resp))
        if not info["token"]:
            return False
        self._token = info["token"]
        self._refresh_token = info["refresh_token"] or self._refresh_token
        return True

    async def _ensure_client_id(self) -> None:
        if self._client_id and self._cust_id:
            return
        ids = parser.client_ids_from_profile(await self._get(api_paths.PROFILE))
        self._cust_id = self._cust_id or ids["cust_id"]
        self._client_id = self._client_id or ids["client_id"]
        if not self._client_id:
            raise AppError(
                ErrorCode.PORTFOLIO_EXTRACTION_FAILED,
                "CardinalStone profile did not include a securities client id",
                status_code=502,
            )

    def _auth_success(self, message: str, mode: str) -> AuthResult:
        self._authenticated = True
        return AuthResult(
            success=True,
            status="authenticated",
            message=message,
            session_metadata={"auth_mode": mode},
            options_patch={SESSION_KEY: self._session_blob()},
        )

    def _session_blob(self) -> dict[str, Any]:
        return {
            "token": self._token,
            "refresh_token": self._refresh_token,
            "cust_id": self._cust_id,
            "client_id": self._client_id,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

    # -------------------------------------------------------------- portfolio

    async def get_accounts(self) -> list[NormalizedAccount]:
        self._require_auth()
        pairs = parser.accounts_from_investment_accounts(
            await self._get(api_paths.investment_accounts(self._client_id or ""))
        )
        self._account_meta = {acct.external_account_id: meta for acct, meta in pairs}
        accounts = [acct for acct, _ in pairs]
        wallet = parser.cash_from_wallet_balance(await self._get(api_paths.WALLET_BALANCE))
        accounts.append(parser.wallet_account(wallet))
        return accounts

    async def get_holdings(self, account: NormalizedAccount) -> list[NormalizedHolding]:
        self._require_auth()
        if account.external_account_id == parser.WALLET_ACCOUNT_ID:
            return []
        payload = await self._get(api_paths.trading_account_report(account.external_account_id, _today()))
        data = parser.unwrap(payload)
        names: dict[str, dict[str, Any]] = {}
        if isinstance(data, dict) and data.get("positionInstruments"):
            try:
                names = {r.get("secId"): r for r in await self._all_equities() if r.get("secId")}
            except AppError:
                logger.warning("CardinalStone equities lookup failed; holdings keep secId as name")
        return parser.holdings_from_trading_report(payload, default_currency=account.currency or "NGN", names=names)

    async def get_cash_balance(self, account: NormalizedAccount) -> Decimal | None:
        self._require_auth()
        if account.external_account_id == parser.WALLET_ACCOUNT_ID:
            return parser.cash_from_wallet_balance(await self._get(api_paths.WALLET_BALANCE))
        return parser.cash_from_account_details(
            await self._get(api_paths.investment_account_details(account.external_account_id))
        )

    async def get_portfolio_summary(self, account: NormalizedAccount) -> PortfolioSummary | None:
        self._require_auth()
        if account.external_account_id == parser.WALLET_ACCOUNT_ID:
            cash = await self.get_cash_balance(account)
            return PortfolioSummary(total_market_value=Decimal("0"), cash_value=cash, currency="NGN")
        cash = await self.get_cash_balance(account)
        report = await self._get(api_paths.trading_account_report(account.external_account_id, _today()))
        return parser.summary_from_trading_report(report, cash=cash)

    async def get_transactions(self, account: NormalizedAccount) -> list[NormalizedTransaction]:
        self._require_auth()
        if account.external_account_id == parser.WALLET_ACCOUNT_ID:
            return []
        end = datetime.now(WAT).date()
        start = end - timedelta(days=365)
        payload = await self._get(
            api_paths.account_statement(account.external_account_id, start.isoformat(), end.isoformat())
        )
        return parser.transactions_from_statement(payload)

    # --------------------------------------------------------------- deposits

    async def get_deposit_instructions(self) -> DepositInstructions:
        self._require_auth()
        payload = await self._get(api_paths.NUBAN_ACCOUNTS)
        try:
            return parser.deposit_instructions_from_nuban(payload)
        except LookupError as exc:
            raise AppError(
                ErrorCode.CONNECTOR_NOT_IMPLEMENTED,
                "CardinalStone has not issued a funding (NUBAN) account for this profile yet",
                status_code=501,
            ) from exc
        except ValueError as exc:
            raise AppError(
                ErrorCode.PORTFOLIO_EXTRACTION_FAILED,
                f"Could not parse CardinalStone funding account details: {exc}",
                status_code=502,
            ) from exc

    # ---------------------------------------------------------- instruments

    async def list_markets(self) -> list[MarketInfo]:
        self._require_auth()
        return [MarketInfo(market_id=api_paths.DEFAULT_MARKET, name="Nigerian Exchange (NGX)", currency="NGN")]

    async def list_instruments(
        self,
        *,
        market_id: str | None = None,
        search: str | None = None,
        page: int = 1,
        per_page: int = 100,
    ) -> InstrumentListResult:
        self._require_auth()
        rows = await self._all_equities()
        return parser.instruments_result(
            rows, market_id=market_id or api_paths.DEFAULT_MARKET, search=search, page=page, per_page=per_page
        )

    async def _all_equities(self) -> list[dict[str, Any]]:
        """Server ignores search and caps size at 100, so page through and cache."""
        if self._equities is not None:
            return self._equities
        rows, pages = parser.equities_page(await self._get(api_paths.equities(0)))
        for p in range(1, min(pages, 20)):
            more, _ = parser.equities_page(await self._get(api_paths.equities(p)))
            rows.extend(more)
        self._equities = rows
        return rows

    # ----------------------------------------------------------------- orders

    async def place_order(self, order: OrderRequest) -> OrderResult:
        self._require_auth()
        symbol = (order.symbol or "").strip().upper()
        if not symbol:
            raise AppError(ErrorCode.VALIDATION_ERROR, "symbol is required", status_code=422)
        if order.order_type == OrderType.LIMIT and (order.limit_price is None or order.limit_price <= 0):
            raise AppError(ErrorCode.VALIDATION_ERROR, "limit_price is required for limit orders", status_code=422)
        if (order.quantity is None or order.quantity <= 0) and (order.amount is None or order.amount <= 0):
            raise AppError(ErrorCode.VALIDATION_ERROR, "quantity (shares) or amount (NGN) must be positive", status_code=422)

        account_id, trading_no = await self._resolve_trading_account(order.account_external_id)
        tif = (order.tif or "DAY").strip().upper()
        if tif in {"GTD", "GOOD_TILL_DATE"}:
            tif = "GOOD_TILL_DATE"
            if not order.expires:
                raise AppError(ErrorCode.VALIDATION_ERROR, "expires (YYYY-MM-DD) is required for GOOD_TILL_DATE", status_code=422)
        elif tif != "DAY":
            raise AppError(ErrorCode.VALIDATION_ERROR, "tif must be DAY or GOOD_TILL_DATE", status_code=422)
        if order.order_type == OrderType.MARKET and tif != "DAY":
            raise AppError(ErrorCode.VALIDATION_ERROR, "MARKET orders are DAY only on CardinalStone", status_code=422)

        limit_price = float(order.limit_price) if order.order_type == OrderType.LIMIT and order.limit_price else None
        qty = order.quantity
        if qty is None or qty <= 0:
            # Amount-based order: ask the broker to convert NGN value -> units (same as web app)
            calc = await self._post(api_paths.ORDERS_CALCULATE, {
                "accountId": account_id,
                "side": order.side.value.upper(),
                "assetType": "EQUITY",
                "marketCode": api_paths.DEFAULT_MARKET,
                "secId": symbol,
                "requestedValue": float(order.amount),
                "requestedQty": None,
                "limitPrice": limit_price,
                "tif": tif,
                "expires": order.expires,
                "tradingAccountNo": trading_no,
            })
            qty = parser.qty_from_calculate(calc)
            if qty is None:
                raise AppError(ErrorCode.PROVIDER_UNAVAILABLE, "CardinalStone did not return a unit quantity for this amount", status_code=502)
        if qty != qty.to_integral_value():
            raise AppError(ErrorCode.VALIDATION_ERROR, "CardinalStone does not allow fractional shares", status_code=422)

        body: dict[str, Any] = {
            "accountId": account_id,
            "tradingAccountNo": trading_no,
            "side": order.side.value.upper(),
            "secId": symbol,
            "requestedQty": int(qty),
            "assetType": "EQUITY",
            "tif": tif,
            "notes": "Placed via Portfolio Aggregator",
        }
        if limit_price is not None:
            body["limitPrice"] = limit_price
        if order.expires:
            body["expires"] = order.expires

        # Review then place — mirrors the web app (both take the same body)
        await self._post(api_paths.VALIDATE_ORDER, body)
        payload = await self._post(api_paths.ORDERS, body)
        return parser.order_result_from_response(payload, side=order.side.value, symbol=symbol)

    async def _resolve_trading_account(self, account_external_id: str | None) -> tuple[str, str | None]:
        if not self._account_meta:
            await self.get_accounts()
        wanted = (account_external_id or "").strip()
        if wanted:
            meta = self._account_meta.get(wanted)
            if meta is None:
                raise AppError(ErrorCode.ACCOUNT_NOT_FOUND, "Account not found on this CardinalStone connection", status_code=404)
            return wanted, meta.get("trading_account_no")
        for acct_id, meta in self._account_meta.items():
            if meta.get("product_code") == "NGX_TRADING" and meta.get("trading_account_no"):
                return acct_id, meta["trading_account_no"]
        raise AppError(ErrorCode.ACCOUNT_NOT_FOUND, "No CardinalStone NGX trading account found", status_code=404)

    # -------------------------------------------------------------- lifecycle

    async def test_connection(self) -> AuthResult:
        """Reachability only (no login)."""
        try:
            await self._ensure_client()
            resp = await self._raw("GET", api_paths.MARKET_IS_OPEN)
            ok = resp.status_code < 500
            return AuthResult(
                success=ok,
                status="authenticated" if ok else "failed",
                message="CardinalStone API reachable" if ok else f"CardinalStone API returned HTTP {resp.status_code}",
            )
        except Exception:
            logger.exception("CardinalStone test_connection failed")
            return AuthResult(success=False, status="failed", message="Unable to reach CardinalStone API")
        finally:
            await self.disconnect()

    async def disconnect(self) -> None:
        self._authenticated = False
        self._token = None
        self._refresh_token = None
        self._account_meta = {}
        self._equities = None
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                logger.exception("Error closing CardinalStone HTTP client (ignored)")
            finally:
                self._client = None

    # -------------------------------------------------------------- internals

    def _require_auth(self) -> None:
        if not self._authenticated or not self._token or self._client is None:
            raise AppError(ErrorCode.INVALID_CREDENTIALS, "Not authenticated with CardinalStone", status_code=401)

    async def _ensure_client(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                follow_redirects=False,
                transport=self.transport,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Origin": api_paths.APP_ORIGIN,
                    "Referer": api_paths.APP_ORIGIN + "/",
                    "User-Agent": _USER_AGENT,
                },
            )

    async def _raw(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        auth: bool = False,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if not _host_allowed(url):
            raise AppError(ErrorCode.NAVIGATION_BLOCKED, "API request blocked: host not in provider allow-list", status_code=400)
        await self._ensure_client()
        assert self._client is not None
        hdrs = dict(headers or {})
        if auth and self._token and "Authorization" not in hdrs:
            hdrs["Authorization"] = f"Bearer {self._token}"
        return await self._client.request(method, url, json=json, headers=hdrs)

    @staticmethod
    def _json(resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except ValueError:
            return None

    async def _get(self, url: str) -> Any:
        return await self._request_json("GET", url)

    async def _post(self, url: str, body: dict[str, Any]) -> Any:
        return await self._request_json("POST", url, body)

    async def _request_json(self, method: str, url: str, body: dict[str, Any] | None = None) -> Any:
        resp = await self._raw(method, url, json=body, auth=True)
        if resp.status_code == 401 and await self._refresh():
            resp = await self._raw(method, url, json=body, auth=True)
        payload = self._json(resp)
        if resp.status_code == 401:
            self._authenticated = False
            raise AppError(ErrorCode.INVALID_CREDENTIALS, "CardinalStone session expired; re-authenticate", status_code=401)
        if resp.status_code >= 500:
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                parser.broker_message(payload) or f"CardinalStone API returned HTTP {resp.status_code}",
                status_code=502,
            )
        if resp.status_code >= 400:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                parser.broker_message(payload) or f"CardinalStone API returned HTTP {resp.status_code}",
                status_code=422,
                details={"broker_status": resp.status_code},
            )
        if payload is None and resp.content:
            raise AppError(ErrorCode.PORTFOLIO_EXTRACTION_FAILED, "CardinalStone API returned non-JSON payload", status_code=502)
        return payload
