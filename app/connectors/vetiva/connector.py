"""Vetiva authenticated-HTTP connector.

Implementation status: SCAFFOLDED (endpoints live-observed 2026-10-31)
---------------------------------------------------------------------
Login, profile, portfolio value/assets/breakdown, wallets, wallet + trading
transactions, securities and order fee-preview were replayed against
``client.vetiva.com/api/proxy`` with the session token. Order placement body
is taken from the portal code; the discovery account had no trade account
(KYC pending), so placement itself could not be exercised.
Flip to OPERATIONAL after ``scripts/record_vetiva_flow.py`` passes.
"""

from __future__ import annotations

import logging
import uuid
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
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def _host_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return bool(host) and host in api_paths.APPROVED_DOMAINS


@register_connector("vetiva")
class VetivaConnector(BrokerConnector):
    provider_id = "vetiva"
    provider_name = "Vetiva"

    transport: httpx.AsyncBaseTransport | None = None  # tests inject MockTransport

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._token: str | None = None
        self._refresh_token: str | None = None
        self._account_id: str | None = None
        self._has_trade_account: bool | None = None
        self._authenticated = False
        self._securities: list[dict[str, Any]] | None = None

    @classmethod
    def metadata(cls) -> ProviderMetadata:
        return ProviderMetadata(
            provider_id="vetiva",
            provider_name="Vetiva",
            connector_type=ConnectorType.AUTHENTICATED_HTTP,
            implementation_status=ImplementationStatus.SCAFFOLDED,
            auth_methods=[AuthMethod.USERNAME_PASSWORD],
            required_credential_fields=[
                CredentialField(name="username", label="Email", field_type="email", required=True, secret=False,
                                description="Vetiva client portal email"),
                CredentialField(name="password", label="Password", field_type="password", required=True, secret=True),
            ],
            optional_credential_fields=[
                CredentialField(name="otp", label="Emailed 2FA code", field_type="otp", required=False, secret=True,
                                description="6-digit code emailed by Vetiva; only when status is mfa_required"),
            ],
            mfa_supported=True,
            capabilities=[
                DataCapability.ACCOUNTS,
                DataCapability.HOLDINGS,
                DataCapability.CASH_BALANCE,
                DataCapability.TRANSACTIONS,
                DataCapability.PORTFOLIO_SUMMARY,
                DataCapability.MFA,
                DataCapability.ORDERS,
                DataCapability.INSTRUMENTS,
            ],
            approved_domains=sorted(api_paths.APPROVED_DOMAINS),
            notes=(
                "Authenticated HTTP via client.vetiva.com/api/proxy (Bearer JWT; refresh token stored "
                "encrypted). Portfolio assets (stocks, fixed income, money market, trusts), wallet + "
                "trading cash, transactions, NGX securities, equity buy/sell (fee preview then place). "
                "Caveats: " + "; ".join(api_paths.INTEGRATION_BLOCKERS)
            ),
            operational=False,
        )

    # ------------------------------------------------------------------ auth

    async def authenticate(self, credentials: dict[str, Any], options: dict[str, Any] | None = None) -> AuthResult:
        options = options or {}
        email = (credentials.get("username") or credentials.get("email") or "").strip()
        password = credentials.get("password") or ""
        otp = (credentials.get("otp") or "").strip()
        try:
            session = options.get(SESSION_KEY)
            if isinstance(session, dict) and session.get("token") and not otp:
                if await self._resume_session(session):
                    return self._auth_success("Authenticated via stored API session", "session")

            if not email or not password:
                return AuthResult(success=False, status="failed", message="Email and password are required")

            # 2FA code supplied: verify-2fa authenticates on its own (email + password + token),
            # so we do not call /auth/login again (that would email a fresh code).
            if otp:
                return await self._verify_2fa(email, password, otp)

            resp = await self._raw("POST", api_paths.LOGIN, json={"email": email, "password": password, "channel": "web"})
            body = self._json(resp)
            if resp.status_code >= 500:
                raise AppError(ErrorCode.PROVIDER_UNAVAILABLE, "Vetiva login service unavailable", status_code=503)
            if resp.status_code >= 400:
                return AuthResult(success=False, status="failed",
                                  message=parser.broker_message(body) or "Authentication failed. Check credentials.")
            info = parser.session_from_auth(body)
            if info["is_2fa_enabled"]:  # never skip 2FA, even if a token came back
                return AuthResult(
                    success=False,
                    status="mfa_required",
                    message="Vetiva emailed a 6-digit code. Submit it via POST /connections/{id}/mfa then re-sync.",
                    mfa_challenge={"type": "email_otp", "provider": "vetiva", "digits": 6},
                )
            if not info["access_token"]:
                return AuthResult(success=False, status="failed", message="Vetiva login returned no session")
            self._apply_session(info)
            await self._load_profile()
            return self._auth_success("Authenticated successfully", "password")
        except AppError:
            raise
        except httpx.TimeoutException as exc:
            await self.disconnect()
            raise AppError(ErrorCode.AUTHENTICATION_TIMEOUT, "Vetiva authentication timed out", status_code=503) from exc
        except Exception as exc:
            logger.exception("Vetiva authenticate failed")
            await self.disconnect()
            raise AppError(ErrorCode.PROVIDER_UNAVAILABLE,
                           "Authentication attempt failed due to a provider or network error", status_code=503) from exc

    async def _verify_2fa(self, email: str, password: str, code: str) -> AuthResult:
        if len(code) != 6 or not code.isdigit():
            return AuthResult(success=False, status="mfa_required", message="2FA code must be 6 digits")
        resp = await self._raw("POST", api_paths.VERIFY_2FA,
                               json={"channel": "web", "email": email, "token": code, "password": password})
        body = self._json(resp)
        info = parser.session_from_auth(body)
        if resp.status_code >= 400 or not info["access_token"]:
            return AuthResult(success=False, status="mfa_required",
                              message=parser.broker_message(body) or "Invalid or expired code")
        self._apply_session(info)
        await self._load_profile()
        return self._auth_success("2FA accepted", "password+2fa")

    async def submit_mfa(self, code: str, challenge: dict[str, Any] | None = None) -> AuthResult:
        # verify-2fa needs email + password, which the sync passes as credentials["otp"] flow
        return AuthResult(success=False, status="mfa_required",
                          message="Submit the emailed code via POST /connections/{id}/mfa; it is used on the next sync")

    async def _resume_session(self, session: dict[str, Any]) -> bool:
        self._token = session.get("token")
        self._refresh_token = session.get("refresh_token")
        self._account_id = session.get("account_id")
        try:
            resp = await self._raw("GET", api_paths.PROFILE, auth=True)
            if resp.status_code in (401, 403) and await self._refresh():
                resp = await self._raw("GET", api_paths.PROFILE, auth=True)
            if resp.status_code >= 400:
                return False
            self._apply_profile(self._json(resp))
            return bool(self._account_id)
        except httpx.HTTPError:
            return False

    async def _refresh(self) -> bool:
        if not self._refresh_token:
            return False
        resp = await self._raw("POST", api_paths.REFRESH, json={"refresh_token": self._refresh_token})
        if resp.status_code >= 400:
            return False
        info = parser.session_from_auth(self._json(resp))
        if not info["access_token"]:
            return False
        self._apply_session(info)
        return True

    def _apply_session(self, info: dict[str, Any]) -> None:
        self._token = info["access_token"]
        self._refresh_token = info["refresh_token"] or self._refresh_token
        self._account_id = info["account_id"] or self._account_id

    async def _load_profile(self) -> None:
        self._apply_profile(await self._get(api_paths.PROFILE))

    def _apply_profile(self, payload: Any) -> None:
        prof = parser.profile_info(payload)
        self._account_id = prof["account_id"] or self._account_id
        self._has_trade_account = prof["has_trade_account"]

    def _auth_success(self, message: str, mode: str) -> AuthResult:
        self._authenticated = True
        return AuthResult(
            success=True,
            status="authenticated",
            message=message,
            session_metadata={"auth_mode": mode, "has_trade_account": bool(self._has_trade_account)},
            options_patch={SESSION_KEY: {
                "token": self._token,
                "refresh_token": self._refresh_token,
                "account_id": self._account_id,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }},
        )

    # -------------------------------------------------------------- portfolio

    async def get_accounts(self) -> list[NormalizedAccount]:
        self._require_auth()
        pv = parser.portfolio_value(await self._get(api_paths.PORTFOLIO_VALUE))
        accounts = [parser.portfolio_account(self._account_id or "main", trading_balance=pv["trading_balance"])]
        accounts.extend(parser.wallet_accounts(await self._get(api_paths.WALLETS)))
        return accounts

    async def get_holdings(self, account: NormalizedAccount) -> list[NormalizedHolding]:
        self._require_auth()
        if not account.external_account_id.startswith(parser.PORTFOLIO_ACCOUNT_PREFIX):
            return []
        rows: list[dict[str, Any]] = []
        page = 1
        while page <= 20:
            batch, total = parser.assets_page(await self._get(api_paths.portfolio_assets(page, 100)))
            rows.extend(batch)
            if not batch or len(rows) >= total:
                break
            page += 1
        return parser.holdings_from_assets(rows)

    async def get_cash_balance(self, account: NormalizedAccount) -> Decimal | None:
        self._require_auth()
        ext = account.external_account_id
        if ext.startswith(parser.WALLET_ACCOUNT_PREFIX):
            return parser.wallet_balance(await self._get(api_paths.WALLETS), ext[len(parser.WALLET_ACCOUNT_PREFIX):])
        return parser.portfolio_value(await self._get(api_paths.PORTFOLIO_VALUE))["trading_balance"]

    async def get_portfolio_summary(self, account: NormalizedAccount) -> PortfolioSummary | None:
        self._require_auth()
        if account.external_account_id.startswith(parser.WALLET_ACCOUNT_PREFIX):
            return PortfolioSummary(total_market_value=Decimal("0"), cash_value=await self.get_cash_balance(account),
                                    currency=account.currency or "NGN")
        return parser.summary_from_breakdown(await self._get(api_paths.PORTFOLIO_VALUE),
                                             await self._get(api_paths.PORTFOLIO_BREAKDOWN))

    async def get_transactions(self, account: NormalizedAccount) -> list[NormalizedTransaction]:
        self._require_auth()
        end = datetime.now(WAT).date()
        start = (end - timedelta(days=365)).isoformat()
        if account.external_account_id.startswith(parser.WALLET_ACCOUNT_PREFIX):
            return parser.transactions_from_records(
                await self._get(api_paths.wallet_transactions(1, 100, start, end.isoformat())), prefix="wallet")
        if not self._has_trade_account:
            return []  # VSL returns 404 until a trade account exists
        return parser.transactions_from_records(
            await self._get(api_paths.trading_transactions(1, 100, start, end.isoformat())), prefix="trade")

    # ---------------------------------------------------------- instruments

    async def list_markets(self) -> list[MarketInfo]:
        self._require_auth()
        return [MarketInfo(market_id=api_paths.DEFAULT_MARKET, name="Nigerian Exchange (NGX)", currency="NGN")]

    async def list_instruments(self, *, market_id: str | None = None, search: str | None = None,
                               page: int = 1, per_page: int = 100) -> InstrumentListResult:
        self._require_auth()
        if market_id and market_id.strip().upper() not in {"NGX", "XNSA"}:
            return InstrumentListResult(market_id=market_id, page=page, per_page=per_page, total=0, instruments=[])
        return parser.instruments_result(await self._all_securities(), search=search, page=page, per_page=per_page)

    async def _all_securities(self) -> list[dict[str, Any]]:
        if self._securities is not None:
            return self._securities
        rows: list[dict[str, Any]] = []
        page = 1
        while page <= 10:
            batch, more = parser.securities_page(await self._get(api_paths.list_securities(page)))
            rows.extend(batch)
            if not more or not batch:
                break
            page += 1
        self._securities = rows
        return rows

    # ----------------------------------------------------------------- orders

    async def place_order(self, order: OrderRequest) -> OrderResult:
        self._require_auth()
        if not self._has_trade_account:
            raise AppError(ErrorCode.CONNECTOR_NOT_IMPLEMENTED,
                           "Vetiva trade account not opened yet (complete KYC in the Vetiva portal)", status_code=501)
        symbol = (order.symbol or "").strip().upper()
        if not symbol:
            raise AppError(ErrorCode.VALIDATION_ERROR, "symbol is required", status_code=422)
        if order.order_type == OrderType.LIMIT and (order.limit_price is None or order.limit_price <= 0):
            raise AppError(ErrorCode.VALIDATION_ERROR, "limit_price is required for limit orders", status_code=422)

        qty: int
        if order.quantity is not None and order.quantity > 0:
            if order.quantity != order.quantity.to_integral_value():
                raise AppError(ErrorCode.VALIDATION_ERROR, "Vetiva does not allow fractional shares", status_code=422)
            qty = int(order.quantity)
        elif order.amount is not None and order.amount > 0:
            price = order.limit_price if order.order_type == OrderType.LIMIT else parser.last_price_from_stats(
                await self._get(api_paths.security_stats(symbol)))
            if not price:
                raise AppError(ErrorCode.PROVIDER_UNAVAILABLE, f"Could not resolve a price for {symbol}", status_code=502)
            qty = parser.units_for_amount(order.amount, price)
            if qty <= 0:
                raise AppError(ErrorCode.VALIDATION_ERROR, "amount is below the price of one share", status_code=422)
        else:
            raise AppError(ErrorCode.VALIDATION_ERROR, "quantity (shares) or amount (NGN) must be positive", status_code=422)

        tif = (order.tif or "DAY").strip().upper()
        if tif in {"GTD", "GOOD_TILL_DATE"}:
            if not order.expires:
                raise AppError(ErrorCode.VALIDATION_ERROR, "expires (YYYY-MM-DD) is required for GTD", status_code=422)
            tif_code = api_paths.TIF_GTD
        elif tif == "DAY":
            tif_code = api_paths.TIF_DAY
        else:
            raise AppError(ErrorCode.VALIDATION_ERROR, "tif must be DAY or GTD", status_code=422)

        body: dict[str, Any] = {
            "type": api_paths.ORDER_TYPE_LIMIT if order.order_type == OrderType.LIMIT else api_paths.ORDER_TYPE_MARKET,
            "symbol": symbol,
            "qty": qty,
            "side": api_paths.SIDE_BUY if order.side == OrderSide.BUY else api_paths.SIDE_SELL,
            "tif": tif_code,
        }
        if tif_code == api_paths.TIF_GTD:
            body["gtd"] = order.expires
        if order.order_type == OrderType.LIMIT:
            body["px"] = format(order.limit_price, "f")  # portal sends price as a string

        # Review (fee preview) then place — same body; idempotency key guards retries
        await self._post(api_paths.ORDER_FEE_PREVIEW, body)
        payload = await self._post(api_paths.ORDER, body, headers={"Idempotency-Key": str(uuid.uuid4())})
        return parser.order_result(payload, side=order.side.value, symbol=symbol)

    # -------------------------------------------------------------- lifecycle

    async def test_connection(self) -> AuthResult:
        try:
            resp = await self._raw("GET", api_paths.LOGIN_PAGE)
            ok = resp.status_code < 500
            return AuthResult(success=ok, status="authenticated" if ok else "failed",
                              message="Vetiva portal reachable" if ok else f"Vetiva returned HTTP {resp.status_code}")
        except Exception:
            logger.exception("Vetiva test_connection failed")
            return AuthResult(success=False, status="failed", message="Unable to reach Vetiva portal")
        finally:
            await self.disconnect()

    async def disconnect(self) -> None:
        # Deliberately no /auth/logout: the stored refresh token keeps scheduled syncs login-free.
        self._authenticated = False
        self._token = None
        self._refresh_token = None
        self._securities = None
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                logger.exception("Error closing Vetiva HTTP client (ignored)")
            finally:
                self._client = None

    # -------------------------------------------------------------- internals

    def _require_auth(self) -> None:
        if not self._authenticated or not self._token:
            raise AppError(ErrorCode.INVALID_CREDENTIALS, "Not authenticated with Vetiva", status_code=401)

    async def _ensure_client(self) -> httpx.AsyncClient:
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
        return self._client

    async def _raw(self, method: str, url: str, *, json: Any = None, auth: bool = False,
                   headers: dict[str, str] | None = None) -> httpx.Response:
        if not _host_allowed(url):
            raise AppError(ErrorCode.NAVIGATION_BLOCKED, "API request blocked: host not in provider allow-list", status_code=400)
        client = await self._ensure_client()
        hdrs = dict(headers or {})
        if auth and self._token:
            hdrs["Authorization"] = f"Bearer {self._token}"
        return await client.request(method, url, json=json, headers=hdrs)

    @staticmethod
    def _json(resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except ValueError:
            return None

    async def _get(self, url: str) -> Any:
        return await self._request_json("GET", url)

    async def _post(self, url: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
        return await self._request_json("POST", url, body, headers)

    async def _request_json(self, method: str, url: str, body: dict[str, Any] | None = None,
                            headers: dict[str, str] | None = None) -> Any:
        resp = await self._raw(method, url, json=body, auth=True, headers=headers)
        if resp.status_code in (401, 403) and await self._refresh():
            resp = await self._raw(method, url, json=body, auth=True, headers=headers)
        payload = self._json(resp)
        if resp.status_code in (401, 403):
            self._authenticated = False
            raise AppError(ErrorCode.INVALID_CREDENTIALS,
                           parser.broker_message(payload) or "Vetiva session expired; re-authenticate", status_code=401)
        if resp.status_code >= 500:
            raise AppError(ErrorCode.PROVIDER_UNAVAILABLE,
                           parser.broker_message(payload) or f"Vetiva API returned HTTP {resp.status_code}", status_code=502)
        if resp.status_code >= 400:
            raise AppError(ErrorCode.VALIDATION_ERROR,
                           parser.broker_message(payload) or f"Vetiva API returned HTTP {resp.status_code}",
                           status_code=422, details={"broker_status": resp.status_code})
        if payload is None and resp.content:
            raise AppError(ErrorCode.PORTFOLIO_EXTRACTION_FAILED, "Vetiva API returned non-JSON payload", status_code=502)
        return payload
