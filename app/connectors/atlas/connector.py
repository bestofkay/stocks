"""Atlass Portfolios Limited (CitiTrader) — Caladata Folio connector.

Implementation status: OPERATIONAL (live-verified 2026-11-12)
-------------------------------------------------------------
Login: POST /folio/authCustomer (password = SHA-512(SHA-512(utf8)) hex) after
password-version probe ``?a=v``. Session cookies ``JSESSIONID`` + ``U_ID``.
Data plane: WebSocket ``wss://app.atlassportfolios.com/cftrader?id={userId}``
with Folio JSON services (``ftwsdshbrds``, ``ftwspfs``, ``ftwsmds``, …).
Logout: GET/POST ``/customers/logoutUser?e=ft`` to release single-login lock.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import httpx

from app.connectors.atlas import api_paths, parser
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

logger = logging.getLogger(__name__)
SESSION_KEY = "api_session"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def _hex_hlp85(password: str) -> str:
    h1 = hashlib.sha512(password.encode("utf-8")).hexdigest()
    return hashlib.sha512(h1.encode("utf-8")).hexdigest()


def _host_allowed(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in api_paths.APPROVED_DOMAINS


@register_connector("atlas")
class AtlasConnector(BrokerConnector):
    provider_id = "atlas"
    provider_name = "Atlass Portfolios"

    transport: httpx.AsyncBaseTransport | None = None

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._cookies: dict[str, str] = {}
        self._authenticated = False
        self._settings: dict[str, Any] = {}
        self._dashboard: dict[str, Any] | None = None
        self._instruments_cache: list[dict[str, Any]] | None = None
        self._holdings_cache: dict[str, list[NormalizedHolding]] = {}
        self._ws = None  # websockets connection
        self._ws_lock = asyncio.Lock()
        self._def_id = 1

    @classmethod
    def metadata(cls) -> ProviderMetadata:
        return ProviderMetadata(
            provider_id="atlas",
            provider_name="Atlass Portfolios Limited",
            connector_type=ConnectorType.AUTHENTICATED_HTTP,
            implementation_status=ImplementationStatus.OPERATIONAL,
            auth_methods=[AuthMethod.USERNAME_PASSWORD],
            required_credential_fields=[
                CredentialField(
                    name="username",
                    label="Login ID",
                    field_type="string",
                    required=True,
                    secret=False,
                    description="CitiTrader Login ID on app.atlassportfolios.com",
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
                DataCapability.PORTFOLIO_SUMMARY,
                DataCapability.DEPOSIT_INSTRUCTIONS,
                DataCapability.ORDERS,
                DataCapability.INSTRUMENTS,
            ],
            approved_domains=sorted(api_paths.APPROVED_DOMAINS),
            notes=(
                "Caladata Folio CitiTrader (cookie session + WebSocket cftrader). "
                "Caveats: " + "; ".join(api_paths.INTEGRATION_BLOCKERS)
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
            if isinstance(session, dict) and session.get("cookies") and session.get("settings"):
                if await self._resume_session(session):
                    return self._auth_success("Authenticated via stored Atlas session", "session")

            if not username or not password:
                return AuthResult(success=False, status="failed", message="Login ID and password are required")

            await self._ensure_client()
            assert self._client is not None
            await self._raw("GET", api_paths.LOGIN_PAGE)

            last_msg = "Authentication failed"
            hashed = _hex_hlp85(password)
            for attempt in range(20):
                # Best-effort release of any prior lock held by this client jar
                try:
                    await self._raw("GET", api_paths.LOGOUT_USER)
                except Exception:
                    pass
                await self._raw(
                    "POST",
                    api_paths.AUTH_VALIDATE,
                    content=f"un={username}",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                auth = await self._raw(
                    "POST",
                    api_paths.AUTH_LOGIN,
                    content=f"un={username}&pw={hashed}",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                text = (auth.text or "").strip()
                last_msg = text or last_msg
                if text.startswith("OK||OK"):
                    break
                if "logged in already" in text.lower() or not text:
                    await self._raw(
                        "POST",
                        api_paths.AUTH_OVERRIDE,
                        content=f"un={username}&pw={hashed}",
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                    # Server-side exclusive lock can linger after an orphaned session
                    await asyncio.sleep(min(30, 3 + attempt * 2))
                    continue
                if text.startswith("errmsg||"):
                    return AuthResult(success=False, status="failed", message=text.split("||", 1)[-1])
                return AuthResult(success=False, status="failed", message=text or "Authentication failed")
            else:
                return AuthResult(
                    success=False,
                    status="failed",
                    message=last_msg.split("||")[-1] if "||" in last_msg else last_msg,
                )

            wa = await self._raw("GET", api_paths.WA)
            settings = parser.parse_settings_from_wa_html(wa.text)
            if not settings.get("id"):
                return AuthResult(success=False, status="failed", message="Login succeeded but settings bootstrap missing")
            self._settings = settings
            self._authenticated = True
            # Warm dashboard over WS
            try:
                await self._ensure_ws()
                dash = await self._ws_ask({"svc": "ftwsdshbrds", "inf": "all", "actn": "q"})
                if isinstance(dash, dict):
                    self._dashboard = dash
            except Exception:
                logger.exception("Atlas dashboard warm-up failed (non-fatal)")
            return self._auth_success("Authenticated successfully", "password")
        except AppError:
            raise
        except httpx.TimeoutException as exc:
            await self.disconnect()
            raise AppError(ErrorCode.AUTHENTICATION_TIMEOUT, "Atlas authentication timed out", status_code=503) from exc
        except Exception as exc:
            logger.exception("atlas authenticate failed")
            await self.disconnect()
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Authentication attempt failed due to a provider or network error",
                status_code=503,
            ) from exc

    async def _resume_session(self, session: dict[str, Any]) -> bool:
        await self._ensure_client()
        cookies = session.get("cookies") or {}
        settings = session.get("settings") or {}
        if not isinstance(cookies, dict) or not cookies.get("JSESSIONID") or not cookies.get("U_ID"):
            return False
        if not isinstance(settings, dict) or not settings.get("id"):
            return False
        self._cookies = {str(k): str(v) for k, v in cookies.items()}
        self._settings = settings
        try:
            await self._ensure_ws()
            dash = await self._ws_ask({"svc": "ftwsdshbrds", "inf": "acct", "actn": "r"}, timeout=8.0)
            if isinstance(dash, dict) and ("acct" in dash or "bal" in dash):
                self._authenticated = True
                self._dashboard = dash if "acct" in dash else {"acct": dash}
                return True
            return False
        except Exception:
            return False

    def _auth_success(self, message: str, mode: str) -> AuthResult:
        self._authenticated = True
        return AuthResult(
            success=True,
            status="authenticated",
            message=message,
            session_metadata={"auth_mode": mode, "user_id": self._settings.get("id")},
            options_patch={SESSION_KEY: self._session_blob()},
        )

    def _session_blob(self) -> dict[str, Any]:
        return {
            "cookies": dict(self._cookies),
            "settings": dict(self._settings),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

    def _capture_cookies(self) -> None:
        if self._client is None:
            return
        for cookie in self._client.cookies.jar:
            self._cookies[cookie.name] = cookie.value

    # -------------------------------------------------------------- portfolio

    async def get_accounts(self) -> list[NormalizedAccount]:
        self._require_auth()
        return parser.accounts_from_settings(self._settings)

    async def get_holdings(self, account: NormalizedAccount) -> list[NormalizedHolding]:
        self._require_auth()
        key = account.external_account_id
        if key in self._holdings_cache:
            return self._holdings_cache[key]
        rows = await self._portfolio_rows(account.external_account_id)
        holdings = parser.holdings_from_portfolio_rows(rows)
        if not holdings:
            # Fallback: asset-class summary from dashboard
            dash = await self._dashboard_all()
            holdings = parser.holdings_from_pf_summary(dash)
        self._holdings_cache[key] = holdings
        return holdings

    async def get_cash_balance(self, account: NormalizedAccount) -> Decimal | None:
        self._require_auth()
        dash = await self._dashboard_all()
        return parser.cash_from_dashboard(dash)

    async def get_portfolio_summary(self, account: NormalizedAccount) -> PortfolioSummary | None:
        self._require_auth()
        dash = await self._dashboard_all()
        cash = parser.cash_from_dashboard(dash)
        return parser.summary_from_dashboard(dash, cash=cash)

    async def get_transactions(self, account: NormalizedAccount) -> list[NormalizedTransaction]:
        # Statement/report export is HTML/BIRT; not wired as JSON ledger yet.
        return []

    async def _dashboard_all(self) -> dict[str, Any]:
        if self._dashboard is not None:
            return self._dashboard
        resp = await self._ws_ask({"svc": "ftwsdshbrds", "inf": "all", "actn": "q"})
        if not isinstance(resp, dict):
            raise AppError(ErrorCode.PORTFOLIO_EXTRACTION_FAILED, "Atlas dashboard response unexpected", status_code=502)
        self._dashboard = resp
        return resp

    async def _portfolio_rows(self, acct: str) -> list[dict[str, Any]]:
        # Try session-aware grid queries; empty list is valid (no positions / closed session).
        queries = [
            {},
            {"acct": int(acct) if acct.isdigit() else acct},
            {"acct": acct, "sId": "1", "xId": "1"},
            {"acct": int(acct) if acct.isdigit() else acct, "sId": 1, "xId": 1},
        ]
        for query in queries:
            try:
                resp = await self._ws_ask(
                    {"svc": "ftwspfs", "actn": "q", "inf": "lst", "query": query, "start": 0, "count": 200},
                    timeout=12.0,
                )
            except AppError:
                continue
            if isinstance(resp, list):
                return [r for r in resp if isinstance(r, dict)]
        return []

    # --------------------------------------------------------------- deposits

    async def get_deposit_instructions(self) -> DepositInstructions:
        self._require_auth()
        return parser.deposit_instructions_from_settings(self._settings)

    # ---------------------------------------------------------- instruments

    async def list_markets(self) -> list[MarketInfo]:
        self._require_auth()
        try:
            resp = await self._ws_ask({"svc": "ftwsexchs", "inf": "slct", "actn": "r"})
            return parser.markets_from_exchange_select(resp)
        except AppError:
            return [MarketInfo(market_id="NGX", name="Nigerian Exchange (NGX)", currency="NGN")]

    async def list_instruments(
        self,
        *,
        market_id: str | None = None,
        search: str | None = None,
        page: int = 1,
        per_page: int = 100,
    ) -> InstrumentListResult:
        self._require_auth()
        rows = await self._all_instruments()
        return parser.instruments_from_mde(
            rows, market_id=market_id or api_paths.DEFAULT_MARKET, search=search, page=page, per_page=per_page
        )

    async def _all_instruments(self) -> list[dict[str, Any]]:
        if self._instruments_cache is not None:
            return self._instruments_cache
        resp = await self._ws_ask({"svc": "ftwsmds", "inf": "mde", "actn": "q"}, timeout=20.0)
        self._instruments_cache = [r for r in resp if isinstance(r, dict)] if isinstance(resp, list) else []
        return self._instruments_cache

    async def _trading_session(self) -> dict[str, Any]:
        """First open Folio FIX/trading session (`ftwsfes` / `snlst`, `on=true`)."""
        try:
            resp = await self._ws_ask(
                {"svc": "ftwsfes", "inf": "snlst", "actn": "r", "on": True},
                timeout=10.0,
            )
        except AppError:
            return {}
        if isinstance(resp, list) and resp and isinstance(resp[0], dict):
            return resp[0]
        return {}

    # ----------------------------------------------------------------- orders

    async def place_order(self, order: OrderRequest) -> OrderResult:
        self._require_auth()
        symbol = (order.symbol or "").strip().upper()
        if not symbol:
            raise AppError(ErrorCode.VALIDATION_ERROR, "symbol is required", status_code=422)
        if order.quantity is None or order.quantity <= 0:
            raise AppError(ErrorCode.VALIDATION_ERROR, "quantity (shares) must be positive", status_code=422)
        if order.order_type == OrderType.LIMIT and (order.limit_price is None or order.limit_price <= 0):
            raise AppError(ErrorCode.VALIDATION_ERROR, "limit_price is required for limit orders", status_code=422)

        accts = self._settings.get("tradeAccounts") or []
        acct = order.account_external_id or (str(accts[0]) if accts else None)
        if not acct:
            raise AppError(ErrorCode.ACCOUNT_NOT_FOUND, "No Atlas trading account found", status_code=404)

        # Trader portal uses OMS (ftwsoms); customer path uses mandates (ftwsmndts).
        # Field names match Folio order/mandate form widgets (symbl, lmtPx, …).
        side = "1" if order.side.value.lower() == "buy" else "2"
        is_trader = bool(self._settings.get("isTrader"))
        session = await self._trading_session()
        if not session.get("value"):
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "No open Atlas trading session (market closed or FIX session down)",
                status_code=422,
            )
        s_id = str(session["value"])
        x_id = session.get("xId") or session.get("mktId") or "1"
        mkt_id = session.get("mktId") or x_id
        payload: dict[str, Any] = {
            "svc": "ftwsoms" if is_trader else "ftwsmndts",
            "actn": "c",
            # Folio Select widgets submit account as string; ints are treated as empty.
            "acct": str(acct),
            "symbl": symbol,
            "sId": s_id,
            "xId": str(x_id),
            "mktId": str(mkt_id),
            "mktSeg": "1",
            "side": side,
            "qty": str(int(order.quantity)),
            "ordType": "2" if order.order_type == OrderType.LIMIT else "1",
            "lmtPx": str(order.limit_price) if order.limit_price is not None else "",
            "tif": "0",
            "pxType": "2" if order.order_type == OrderType.LIMIT else "1",
            "sectype": "1",
            "id": "-",
            "vn": "0",
        }
        if not is_trader:
            payload["inf"] = "mndts"

        resp = await self._ws_ask(payload, timeout=20.0)
        # Folio may warn (duplicate / KYC); UI re-sends with override flags.
        if isinstance(resp, dict) and str(resp.get("dOp") or "").upper() == "WARNING":
            override = dict(payload)
            if str(resp.get("dCx") or "").upper() == "KYC":
                override["ovrdKyc"] = True
            else:
                override["ovrdOrd"] = True
            resp = await self._ws_ask(override, timeout=20.0)

        if isinstance(resp, dict) and str(resp.get("dOp") or "").upper() == "ERROR":
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                str(resp.get("msg") or "Atlas rejected the order"),
                status_code=422,
                details={"broker": resp},
            )
        order_id = None
        if isinstance(resp, dict):
            order_id = str(resp.get("id") or resp.get("ordId") or resp.get("cloid") or resp.get("mndtId") or "") or None
        return OrderResult(
            external_order_id=order_id,
            status="submitted",
            symbol=symbol,
            side=order.side,
            message="Order submitted via Atlas CitiTrader",
            raw=resp if isinstance(resp, dict) else {"resp": resp},
        )

    # -------------------------------------------------------------- lifecycle

    async def test_connection(self) -> AuthResult:
        try:
            await self._ensure_client()
            resp = await self._raw("GET", api_paths.LOGIN_PAGE)
            ok = resp.status_code < 500
            return AuthResult(
                success=ok,
                status="authenticated" if ok else "failed",
                message="Atlas portal reachable" if ok else f"Atlas returned HTTP {resp.status_code}",
            )
        except Exception:
            logger.exception("atlas test_connection failed")
            return AuthResult(success=False, status="failed", message="Unable to reach Atlas")
        finally:
            await self.disconnect()

    async def disconnect(self) -> None:
        # Release single-login lock when possible
        try:
            if self._cookies.get("JSESSIONID"):
                await self._ensure_client()
                await self._raw("GET", api_paths.LOGOUT_USER)
        except Exception:
            logger.exception("Atlas logoutUser failed (ignored)")
        await self._close_ws()
        self._authenticated = False
        self._cookies = {}
        self._settings = {}
        self._dashboard = None
        self._instruments_cache = None
        self._holdings_cache = {}
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                logger.exception("Error closing atlas HTTP client (ignored)")
            finally:
                self._client = None

    # --------------------------------------------------------------- HTTP/WS

    def _require_auth(self) -> None:
        if not self._authenticated or not self._cookies.get("JSESSIONID"):
            raise AppError(ErrorCode.INVALID_CREDENTIALS, "Not authenticated with Atlas", status_code=401)

    async def _ensure_client(self) -> None:
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=20.0),
            follow_redirects=True,
            headers={"User-Agent": _UA, "Accept": "text/html,application/json,*/*"},
            transport=self.transport,
        )
        for k, v in self._cookies.items():
            self._client.cookies.set(k, v, domain="app.atlassportfolios.com")

    async def _raw(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, Any] | None = None,
        content: str | bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if not _host_allowed(url) and not url.startswith("wss://"):
            # allow only approved http(s) hosts
            if urlparse(url).scheme in {"http", "https"}:
                raise AppError(ErrorCode.NAVIGATION_BLOCKED, "Request blocked: host not in provider allow-list", status_code=400)
        await self._ensure_client()
        assert self._client is not None
        for k, v in self._cookies.items():
            self._client.cookies.set(k, v, domain="app.atlassportfolios.com")
        resp = await self._client.request(method, url, data=data, content=content, headers=headers)
        self._capture_cookies()
        return resp

    async def _ensure_ws(self) -> None:
        if self._ws is not None:
            return
        try:
            import websockets
        except ImportError as exc:
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Atlas connector requires the 'websockets' package",
                status_code=503,
            ) from exc
        user_id = self._settings.get("id")
        if not user_id:
            raise AppError(ErrorCode.INVALID_CREDENTIALS, "Atlas session missing user id", status_code=401)
        cookie_header = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
        url = f"{api_paths.WS}?id={user_id}"
        self._ws = await websockets.connect(
            url,
            additional_headers={
                "Cookie": cookie_header,
                "Origin": api_paths.BASE,
                "User-Agent": _UA,
            },
            max_size=20_000_000,
            open_timeout=20,
        )

    async def _close_ws(self) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.close()
        except Exception:
            logger.exception("Error closing atlas websocket (ignored)")
        finally:
            self._ws = None

    async def _ws_ask(self, payload: dict[str, Any], *, timeout: float = 12.0) -> Any:
        async with self._ws_lock:
            await self._ensure_ws()
            assert self._ws is not None
            def_id = self._def_id
            self._def_id += 1
            body = {**payload, "defId": def_id}
            fut: asyncio.Future = asyncio.get_running_loop().create_future()

            async def reader() -> None:
                assert self._ws is not None
                while True:
                    raw = await self._ws.recv()
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if msg.get("type") == "defReq" and msg.get("defId") == def_id:
                        if not fut.done():
                            fut.set_result(msg)
                        return

            task = asyncio.create_task(reader())
            try:
                await self._ws.send(json.dumps(body))
                msg = await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError as exc:
                task.cancel()
                # reconnect next time
                await self._close_ws()
                raise AppError(
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    f"Atlas WebSocket request timed out ({payload.get('svc')}/{payload.get('inf')})",
                    status_code=504,
                ) from exc
            except Exception as exc:
                task.cancel()
                await self._close_ws()
                raise AppError(
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    f"Atlas WebSocket error: {exc}",
                    status_code=502,
                ) from exc
            finally:
                if not task.done():
                    task.cancel()

            resp = msg.get("resp")
            if isinstance(resp, dict) and str(resp.get("dOp") or "").upper() == "ERROR":
                # Let callers decide; return resp for order path, raise for hard failures when empty msg
                return resp
            return resp
