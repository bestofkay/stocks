"""InvestNaija authenticated-HTTP connector (Zanibal CHD API).

Implementation status: OPERATIONAL (live-verified 2026-11-05)
-------------------------------------------------------------
Login (RSA-OAEP password), TradeIN portfolios/holdings/cash, wallet NUBAN
deposit instructions, NGX securities list, and order fee preview
(``calculate-price``) verified live. Equity ``trade-order/create`` reaches
the investing service but currently returns a broker-side JSON deserialization
error for the observed payload (CHD validates numeric prices; the downstream
``/investing/api/v1/orders`` service rejects the same body). Deposit + sync +
markets work; live buy/sell placement depends on that broker gateway.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

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


def encrypt_password(plaintext: str) -> str:
    """RSA-OAEP(SHA-256) encrypt password with the app's embedded public key."""
    der = base64.b64decode(api_paths.PASSWORD_PUBLIC_KEY_B64)
    key = serialization.load_der_public_key(der)
    ct = key.encrypt(
        plaintext.encode("utf-8"),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(ct).decode("ascii")


@register_connector("investnaija")
class InvestNaijaConnector(BrokerConnector):
    provider_id = "investnaija"
    provider_name = "InvestNaija"

    transport: httpx.AsyncBaseTransport | None = None

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._token: str | None = None
        self._uuid_token: str | None = None
        self._authenticated = False
        self._account_meta: dict[str, dict[str, Any]] = {}
        self._securities: list[dict[str, Any]] | None = None
        self._order_terms: list[dict[str, Any]] | None = None

    @classmethod
    def metadata(cls) -> ProviderMetadata:
        return ProviderMetadata(
            provider_id="investnaija",
            provider_name="InvestNaija",
            connector_type=ConnectorType.AUTHENTICATED_HTTP,
            implementation_status=ImplementationStatus.OPERATIONAL,
            auth_methods=[AuthMethod.USERNAME_PASSWORD],
            required_credential_fields=[
                CredentialField(
                    name="username",
                    label="Email",
                    field_type="email",
                    required=True,
                    secret=False,
                    description="Email used on app.investnaija.com",
                ),
                CredentialField(
                    name="password",
                    label="Password",
                    field_type="password",
                    required=True,
                    secret=True,
                ),
            ],
            optional_credential_fields=[],
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
                "Authenticated HTTP against invest-chd.zanibal.com (JWT + x-uuid-token; "
                "RSA-OAEP login password). TradeIN portfolios, wallet NUBAN funding, "
                "NGX equities, fee preview. Caveats: " + "; ".join(api_paths.INTEGRATION_BLOCKERS)
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
        try:
            session = options.get(SESSION_KEY)
            if isinstance(session, dict) and session.get("token") and session.get("uuid_token"):
                if await self._resume_session(session):
                    return self._auth_success("Authenticated via stored API session", "session")

            if not username or not password:
                return AuthResult(success=False, status="failed", message="Email and password are required")

            await self._ensure_client()
            enc = encrypt_password(password)
            resp = await self._raw(
                "POST",
                api_paths.LOGIN,
                json={"email": username, "password": enc, "rememberMe": True},
            )
            body = self._json(resp)
            if resp.status_code >= 500:
                raise AppError(ErrorCode.PROVIDER_UNAVAILABLE, "InvestNaija login service unavailable", status_code=503)
            info = parser.login_tokens(body)
            if resp.status_code >= 400 or not info["token"] or not info["uuid_token"]:
                return AuthResult(
                    success=False,
                    status="failed",
                    message=info.get("message") or parser.broker_message(body) or "Authentication failed. Check credentials.",
                )
            if info["two_factor"]:
                return AuthResult(
                    success=False,
                    status="mfa_required",
                    message="InvestNaija 2FA is enabled on this account; MFA submission is not automated yet.",
                    mfa_challenge={"type": "totp", "provider": "investnaija"},
                )

            self._token = str(info["token"])
            self._uuid_token = str(info["uuid_token"])
            # Quick probe
            probe = await self._raw("GET", api_paths.PROFILE, auth=True)
            if probe.status_code >= 400:
                return AuthResult(
                    success=False,
                    status="failed",
                    message=parser.broker_message(self._json(probe)) or "Login succeeded but session was rejected",
                )
            return self._auth_success("Authenticated successfully", "password")
        except AppError:
            raise
        except httpx.TimeoutException as exc:
            await self.disconnect()
            raise AppError(ErrorCode.AUTHENTICATION_TIMEOUT, "InvestNaija authentication timed out", status_code=503) from exc
        except Exception as exc:
            logger.exception("InvestNaija authenticate failed")
            await self.disconnect()
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Authentication attempt failed due to a provider or network error",
                status_code=503,
            ) from exc

    async def _resume_session(self, session: dict[str, Any]) -> bool:
        await self._ensure_client()
        self._token = session.get("token")
        self._uuid_token = session.get("uuid_token")
        try:
            resp = await self._raw("GET", api_paths.PROFILE, auth=True)
            if resp.status_code == 401 and await self._refresh():
                resp = await self._raw("GET", api_paths.PROFILE, auth=True)
            return resp.status_code < 400
        except httpx.HTTPError:
            return False

    async def _refresh(self) -> bool:
        if not self._token or not self._uuid_token:
            return False
        resp = await self._raw("POST", api_paths.REFRESH, json={}, auth=True)
        if resp.status_code >= 400:
            return False
        info = parser.login_tokens(self._json(resp))
        if info.get("token"):
            self._token = str(info["token"])
        if info.get("uuid_token"):
            self._uuid_token = str(info["uuid_token"])
        return bool(self._token and self._uuid_token)

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
            "uuid_token": self._uuid_token,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

    # -------------------------------------------------------------- portfolio

    async def get_accounts(self) -> list[NormalizedAccount]:
        self._require_auth()
        rows = parser.trade_portfolio_rows(await self._get(api_paths.TRADE_PORTFOLIOS))
        accounts: list[NormalizedAccount] = []
        self._account_meta = {}
        for row in rows:
            acct, meta = parser.account_from_trade_portfolio(row)
            if not acct.external_account_id:
                continue
            self._account_meta[acct.external_account_id] = {**meta, "row": row}
            accounts.append(acct)
        # Wallet (cash funding)
        try:
            wallet = await self._get(api_paths.WALLET_BALANCE)
            accounts.append(parser.wallet_account(parser.cash_from_wallet_balance(wallet)))
        except AppError:
            logger.warning("InvestNaija wallet balance unavailable; skipping wallet account")
        return accounts

    async def get_holdings(self, account: NormalizedAccount) -> list[NormalizedHolding]:
        self._require_auth()
        if account.external_account_id == parser.WALLET_ACCOUNT_ID:
            return []
        meta = await self._meta_for(account.external_account_id)
        row = meta.get("row")
        if isinstance(row, dict) and row.get("portfolioHoldings"):
            return parser.holdings_from_trade_portfolio(row, default_currency=account.currency or "NGN")
        # Fallback: detail with x-secure-token
        detail = await self._get_secure(api_paths.trade_portfolio(account.external_account_id), meta.get("signature"))
        return parser.holdings_from_detail(detail, default_currency=account.currency or "NGN")

    async def get_cash_balance(self, account: NormalizedAccount) -> Decimal | None:
        self._require_auth()
        if account.external_account_id == parser.WALLET_ACCOUNT_ID:
            return parser.cash_from_wallet_balance(await self._get(api_paths.WALLET_BALANCE))
        meta = await self._meta_for(account.external_account_id)
        row = meta.get("row")
        if isinstance(row, dict):
            cash = parser.cash_from_trade_row(row)
            if cash is not None:
                return cash
        detail = await self._get_secure(api_paths.trade_portfolio(account.external_account_id), meta.get("signature"))
        return parser.cash_from_detail(detail)

    async def get_portfolio_summary(self, account: NormalizedAccount) -> PortfolioSummary | None:
        self._require_auth()
        cash = await self.get_cash_balance(account)
        if account.external_account_id == parser.WALLET_ACCOUNT_ID:
            return PortfolioSummary(total_market_value=Decimal("0"), cash_value=cash, currency="NGN")
        meta = await self._meta_for(account.external_account_id)
        row = meta.get("row")
        if isinstance(row, dict):
            return parser.summary_from_trade_row(row, cash=cash)
        detail = await self._get_secure(api_paths.trade_portfolio(account.external_account_id), meta.get("signature"))
        return parser.summary_from_detail(detail, cash=cash)

    async def get_transactions(self, account: NormalizedAccount) -> list[NormalizedTransaction]:
        self._require_auth()
        # Wallet/trade activity shares the customer transactions feed
        try:
            return parser.transactions_from_wallet(await self._get(api_paths.my_transactions(1, 50)))
        except AppError:
            return []

    # --------------------------------------------------------------- deposits

    async def get_deposit_instructions(self) -> DepositInstructions:
        self._require_auth()
        payload = await self._get(api_paths.WALLET_BALANCE)
        try:
            return parser.deposit_instructions_from_wallet(payload)
        except LookupError as exc:
            raise AppError(
                ErrorCode.CONNECTOR_NOT_IMPLEMENTED,
                "InvestNaija has not issued a wallet virtual account (NUBAN) for this profile yet",
                status_code=501,
            ) from exc
        except ValueError as exc:
            raise AppError(
                ErrorCode.PORTFOLIO_EXTRACTION_FAILED,
                f"Could not parse InvestNaija funding account details: {exc}",
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
        rows = await self._all_securities()
        return parser.instruments_result(
            rows, market_id=market_id or api_paths.DEFAULT_MARKET, search=search, page=page, per_page=per_page
        )

    async def _all_securities(self) -> list[dict[str, Any]]:
        if self._securities is not None:
            return self._securities
        self._securities = parser.securities_rows(await self._get(api_paths.SECURITIES))
        return self._securities

    async def _day_term(self) -> dict[str, Any]:
        if self._order_terms is None:
            payload = await self._get(api_paths.ORDER_TERMS)
            data = parser.unwrap(payload)
            rows = []
            if isinstance(data, dict):
                rows = data.get("result") or []
            self._order_terms = [r for r in rows if isinstance(r, dict)]
        term = parser.day_order_term({"data": {"result": self._order_terms}})
        if not term:
            raise AppError(ErrorCode.PROVIDER_UNAVAILABLE, "InvestNaija order terms unavailable", status_code=502)
        return term

    # ----------------------------------------------------------------- orders

    async def place_order(self, order: OrderRequest) -> OrderResult:
        self._require_auth()
        symbol = (order.symbol or "").strip().upper()
        if not symbol:
            raise AppError(ErrorCode.VALIDATION_ERROR, "symbol is required", status_code=422)
        if order.order_type == OrderType.LIMIT and (order.limit_price is None or order.limit_price <= 0):
            raise AppError(ErrorCode.VALIDATION_ERROR, "limit_price is required for limit orders", status_code=422)
        if order.quantity is None or order.quantity <= 0:
            raise AppError(ErrorCode.VALIDATION_ERROR, "quantity (shares) must be positive", status_code=422)
        if order.quantity != order.quantity.to_integral_value():
            raise AppError(ErrorCode.VALIDATION_ERROR, "InvestNaija does not allow fractional shares", status_code=422)

        account_id, meta = await self._resolve_trade_account(order.account_external_id)
        sig = meta.get("signature")
        if not sig:
            raise AppError(ErrorCode.PORTFOLIO_EXTRACTION_FAILED, "TradeIN portfolio signature missing", status_code=502)

        sec = await self._security_by_symbol(symbol)
        if not sec:
            raise AppError(ErrorCode.VALIDATION_ERROR, f"Unknown InvestNaija symbol: {symbol}", status_code=422)

        term = await self._day_term()
        last_px = parser.dec(sec.get("lastPx") or sec.get("price") or sec.get("close")) or Decimal("0")
        if order.order_type == OrderType.LIMIT:
            px = float(order.limit_price)  # type: ignore[arg-type]
            order_type = "LIMIT"
            price_type = "LIMIT"
        else:
            px = float(last_px) if last_px > 0 else 0.0
            if px <= 0:
                raise AppError(ErrorCode.VALIDATION_ERROR, "Could not resolve a market price for this symbol", status_code=422)
            order_type = "MARKET"
            price_type = "MARKET"

        body: dict[str, Any] = {
            "portfolioId": account_id,
            "securityId": sec.get("id"),
            "securityName": sec.get("secId") or symbol,
            "symbol": symbol,
            "quantityRequested": int(order.quantity),
            "orderType": order_type,
            "priceType": price_type,
            "orderTermId": term.get("id"),
            "orderTermName": term.get("name"),
            "side": order.side.value.upper(),
            "marketPrice": px,
            "limitPrice": px,
            "price": px,
        }

        # Fee preview (live-verified)
        await self._post_secure(api_paths.CALCULATE_PRICE, body, signature=str(sig))

        # Placement — broker investing gateway may still reject (see module docstring)
        payload = await self._post_secure(api_paths.CREATE_ORDER, body, signature=str(sig))
        return parser.order_result_from_response(payload, side=order.side.value, symbol=symbol)

    async def _security_by_symbol(self, symbol: str) -> dict[str, Any] | None:
        rows = await self._all_securities()
        symbol = symbol.upper()
        for row in rows:
            if (row.get("symbol") or row.get("secId") or "").upper() == symbol:
                return row
        return None

    async def _resolve_trade_account(self, account_external_id: str | None) -> tuple[str, dict[str, Any]]:
        if not self._account_meta:
            await self.get_accounts()
        wanted = (account_external_id or "").strip()
        if wanted and wanted != parser.WALLET_ACCOUNT_ID:
            meta = self._account_meta.get(wanted)
            if meta is None:
                raise AppError(ErrorCode.ACCOUNT_NOT_FOUND, "Account not found on this InvestNaija connection", status_code=404)
            return wanted, meta
        for acct_id, meta in self._account_meta.items():
            if acct_id != parser.WALLET_ACCOUNT_ID and meta.get("signature"):
                return acct_id, meta
        raise AppError(ErrorCode.ACCOUNT_NOT_FOUND, "No InvestNaija TradeIN portfolio found", status_code=404)

    async def _meta_for(self, account_external_id: str) -> dict[str, Any]:
        if account_external_id not in self._account_meta:
            await self.get_accounts()
        meta = self._account_meta.get(account_external_id)
        if meta is None:
            raise AppError(ErrorCode.ACCOUNT_NOT_FOUND, "Account not found on this InvestNaija connection", status_code=404)
        return meta

    # -------------------------------------------------------------- lifecycle

    async def test_connection(self) -> AuthResult:
        try:
            await self._ensure_client()
            resp = await self._raw("GET", api_paths.SECURITIES)
            ok = resp.status_code < 500
            return AuthResult(
                success=ok,
                status="authenticated" if ok else "failed",
                message="InvestNaija API reachable" if ok else f"InvestNaija API returned HTTP {resp.status_code}",
            )
        except Exception:
            logger.exception("InvestNaija test_connection failed")
            return AuthResult(success=False, status="failed", message="Unable to reach InvestNaija API")
        finally:
            await self.disconnect()

    async def disconnect(self) -> None:
        self._authenticated = False
        self._token = None
        self._uuid_token = None
        self._account_meta = {}
        self._securities = None
        self._order_terms = None
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                logger.exception("Error closing InvestNaija HTTP client (ignored)")
            finally:
                self._client = None

    # -------------------------------------------------------------- internals

    def _require_auth(self) -> None:
        if not self._authenticated or not self._token or not self._uuid_token or self._client is None:
            raise AppError(ErrorCode.INVALID_CREDENTIALS, "Not authenticated with InvestNaija", status_code=401)

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
        if auth and self._token:
            # InvestNaija uses raw JWT (no "Bearer " prefix)
            hdrs.setdefault("Authorization", self._token)
            if self._uuid_token:
                hdrs.setdefault("x-uuid-token", self._uuid_token)
        return await self._client.request(method, url, json=json, headers=hdrs)

    @staticmethod
    def _json(resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except ValueError:
            return None

    async def _get(self, url: str) -> Any:
        return await self._request_json("GET", url)

    async def _get_secure(self, url: str, signature: str | None) -> Any:
        extra = {"x-secure-token": signature} if signature else None
        return await self._request_json("GET", url, headers=extra)

    async def _post_secure(self, url: str, body: dict[str, Any], *, signature: str) -> Any:
        return await self._request_json("POST", url, body, headers={"x-secure-token": signature})

    async def _request_json(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        resp = await self._raw(method, url, json=body, auth=True, headers=headers)
        if resp.status_code == 401 and await self._refresh():
            resp = await self._raw(method, url, json=body, auth=True, headers=headers)
        payload = self._json(resp)
        if resp.status_code == 401:
            self._authenticated = False
            raise AppError(ErrorCode.INVALID_CREDENTIALS, "InvestNaija session expired; re-authenticate", status_code=401)
        if resp.status_code >= 500:
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                parser.broker_message(payload) or f"InvestNaija API returned HTTP {resp.status_code}",
                status_code=502,
            )
        if resp.status_code >= 400:
            msg = parser.broker_message(payload) or f"InvestNaija API returned HTTP {resp.status_code}"
            # Broker investing gateway deserialization failure (observed on create)
            if "Failed to read request" in msg:
                raise AppError(
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    "InvestNaija order gateway rejected the trade payload (broker-side deserialize error). "
                    "Fee preview works; placement is blocked until the broker accepts the create body.",
                    status_code=502,
                    details={"broker_status": resp.status_code, "broker_message": msg},
                )
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                msg,
                status_code=422,
                details={"broker_status": resp.status_code},
            )
        if payload is None and resp.content:
            raise AppError(ErrorCode.PORTFOLIO_EXTRACTION_FAILED, "InvestNaija API returned non-JSON payload", status_code=502)
        return payload
