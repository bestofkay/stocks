"""Shared NaYa OMS connector base — ASP.NET WebForms + ASMX JSON.

Subclass with ClassVars (provider_id, origin, approved_domains, …) and register
with ``@register_connector``. Used by Fundvine, ICON Stock Brokers, and other
white-label etrade portals on the same stack.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, ClassVar
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
    OrderType,
    PortfolioSummary,
    ProviderMetadata,
)
from app.core.errors import AppError, ErrorCode

from . import parser
from .paths import COMMON_BLOCKERS, NaYaPaths, build_paths

logger = logging.getLogger(__name__)
WAT = timezone(timedelta(hours=1))
SESSION_KEY = "api_session"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def _host_allowed(url: str, approved: frozenset[str]) -> bool:
    return (urlparse(url).hostname or "").lower() in approved


def _form_value(html: str, name: str) -> str:
    m = re.search(rf'id="{re.escape(name)}"[^>]*value="([^"]*)"', html) or re.search(
        rf'name="{re.escape(name)}"[^>]*value="([^"]*)"', html
    )
    return m.group(1) if m else ""


class NaYaOMSConnector(BrokerConnector):
    """Subclass and set the ClassVars; register with @register_connector."""

    provider_id: ClassVar[str] = ""
    provider_name: ClassVar[str] = ""
    origin: ClassVar[str] = ""
    approved_domains: ClassVar[frozenset[str]] = frozenset()
    username_label: ClassVar[str] = "Username or email"
    portal_label: ClassVar[str] = ""  # shown in credential description
    extra_blockers: ClassVar[list[str]] = []
    implementation_status: ClassVar[ImplementationStatus] = ImplementationStatus.OPERATIONAL
    _paths: ClassVar[NaYaPaths | None] = None

    transport: httpx.AsyncBaseTransport | None = None

    @classmethod
    def paths(cls) -> NaYaPaths:
        if cls._paths is None:
            if not cls.origin:
                raise RuntimeError(f"{cls.__name__} missing origin")
            cls._paths = build_paths(cls.origin, extra_domains=cls.approved_domains)
        return cls._paths

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._cookies: dict[str, str] = {}
        self._authenticated = False
        self._account_meta: dict[str, dict[str, Any]] = {}
        self._client_id: int | None = None
        self._securities: list[dict[str, Any]] | None = None
        self._positions_cache: dict[str, dict[str, Any]] = {}

    @classmethod
    def metadata(cls) -> ProviderMetadata:
        portal = cls.portal_label or cls.origin.replace("https://", "")
        return ProviderMetadata(
            provider_id=cls.provider_id,
            provider_name=cls.provider_name,
            connector_type=ConnectorType.AUTHENTICATED_HTTP,
            implementation_status=cls.implementation_status,
            auth_methods=[AuthMethod.USERNAME_PASSWORD],
            required_credential_fields=[
                CredentialField(
                    name="username",
                    label=cls.username_label,
                    field_type="string",
                    required=True,
                    secret=False,
                    description=f"Same identifier used on {portal}",
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
            approved_domains=sorted(cls.approved_domains),
            notes=(
                f"Authenticated HTTP against {cls.provider_name} NaYa OMS (cookie session + ASMX JSON). "
                "CSCS portfolios, ledger, NGX securities, Paystack card funding, EqtyMandate orders. "
                "Caveats: " + "; ".join(COMMON_BLOCKERS + list(cls.extra_blockers))
            ),
            operational=cls.implementation_status == ImplementationStatus.OPERATIONAL,
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
            if isinstance(session, dict) and session.get("cookies"):
                if await self._resume_session(session):
                    return self._auth_success(f"Authenticated via stored {self.provider_name} session", "session")

            if not username or not password:
                return AuthResult(success=False, status="failed", message="Username and password are required")

            await self._ensure_client()
            assert self._client is not None
            login_page = await self._raw("GET", self.paths().login)
            html = login_page.text
            tok = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]*)"', html)
            data = {
                "__VIEWSTATE": _form_value(html, "__VIEWSTATE"),
                "__VIEWSTATEGENERATOR": _form_value(html, "__VIEWSTATEGENERATOR"),
                "__EVENTVALIDATION": _form_value(html, "__EVENTVALIDATION"),
                "__RequestVerificationToken": tok.group(1) if tok else "",
                "txtusername": username,
                "txtpassword": password,
                "chkrememberme": "on",
                "btnsignin": "Sign In",
                "hdfshown": "login",
            }
            resp = await self._raw(
                "POST",
                self.paths().login,
                data=data,
                headers={"Referer": self.paths().login, "Origin": self.paths().base},
            )
            self._capture_cookies()
            if "__NAYA.AUTH" not in self._cookies:
                msg = "Authentication failed. Check credentials."
                err = re.search(r'id="[^"]*error[^"]*"[^>]*>([^<]+)', resp.text, re.I)
                if err and err.group(1).strip():
                    msg = err.group(1).strip()
                return AuthResult(success=False, status="failed", message=msg)

            # Land on home + capture ClientId from markup when present
            home = await self._raw("GET", self.paths().default)
            self._capture_cookies()
            m = re.search(r"ClientId:\s*'(\d+)'", home.text) or re.search(r'ClientId:\s*"(\d+)"', home.text)
            if m:
                self._client_id = int(m.group(1))
            probe = await self._asmx(self.paths().get_custom_app_name, {})
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
            raise AppError(ErrorCode.AUTHENTICATION_TIMEOUT, f"{self.provider_name} authentication timed out", status_code=503) from exc
        except Exception as exc:
            logger.exception("%s authenticate failed", self.provider_id)
            await self.disconnect()
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Authentication attempt failed due to a provider or network error",
                status_code=503,
            ) from exc

    async def _resume_session(self, session: dict[str, Any]) -> bool:
        await self._ensure_client()
        cookies = session.get("cookies") or {}
        if not isinstance(cookies, dict) or not cookies.get("__NAYA.AUTH"):
            return False
        self._cookies = {str(k): str(v) for k, v in cookies.items()}
        self._client_id = session.get("client_id") if isinstance(session.get("client_id"), int) else None
        try:
            resp = await self._asmx(self.paths().get_custom_app_name, {})
            if resp.status_code == 401:
                return False
            ok = resp.status_code < 400 and "__NAYA.AUTH" in self._cookies
            if ok:
                self._authenticated = True
            return ok
        except httpx.HTTPError:
            return False

    def _auth_success(self, message: str, mode: str) -> AuthResult:
        self._authenticated = True
        return AuthResult(
            success=True,
            status="authenticated",
            message=message,
            session_metadata={"auth_mode": mode, "client_id": self._client_id},
            options_patch={SESSION_KEY: self._session_blob()},
        )

    def _session_blob(self) -> dict[str, Any]:
        return {
            "cookies": dict(self._cookies),
            "client_id": self._client_id,
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
        # Prefer ClientId from session; empty CSCSNumber still returns the list
        payload = await self._post_asmx(
            self.paths().get_cscs_accounts,
            {"req": {"ClientId": self._client_id or 0, "CSCSNumber": ""}},
        )
        rows = parser.cscs_accounts(payload)
        if not rows and self._client_id:
            # retry with just client id shape already used
            pass
        out: list[NormalizedAccount] = []
        self._account_meta = {}
        for row in rows:
            acct, meta = parser.account_from_cscs(row)
            if meta.get("client_id") and not self._client_id:
                try:
                    self._client_id = int(meta["client_id"])
                except (TypeError, ValueError):
                    pass
            self._account_meta[acct.external_account_id] = meta
            out.append(acct)
        return out

    async def get_holdings(self, account: NormalizedAccount) -> list[NormalizedHolding]:
        self._require_auth()
        pos = await self._positions_for(account.external_account_id)
        return parser.holdings_from_positions(pos)

    async def get_cash_balance(self, account: NormalizedAccount) -> Decimal | None:
        self._require_auth()
        pos = await self._positions_for(account.external_account_id)
        cash = parser.cash_from_positions(pos)
        if cash is not None:
            return cash
        # Fallback: purchase info cleared/OMS balance
        info = await self._post_asmx(
            self.paths().get_purchase_info,
            {"req": {"AccountNumber": account.external_account_id}},
        )
        data = parser.unwrap_d(info)
        if isinstance(data, dict):
            pi = data.get("PurchaseInfo") if isinstance(data.get("PurchaseInfo"), dict) else data
            return parser.dec(pi.get("OMSBalance") or pi.get("ClearedBalance") or pi.get("OMSBalanceObj"))
        return None

    async def get_portfolio_summary(self, account: NormalizedAccount) -> PortfolioSummary | None:
        self._require_auth()
        cash = await self.get_cash_balance(account)
        pos = await self._positions_for(account.external_account_id)
        return parser.summary_from_positions(pos, cash=cash)

    async def get_transactions(self, account: NormalizedAccount) -> list[NormalizedTransaction]:
        self._require_auth()
        meta = await self._meta_for(account.external_account_id)
        today = datetime.now(WAT).date()
        start = (today - timedelta(days=365)).isoformat()
        end = today.isoformat()
        # NaYa date pickers often use yyyy-MM-dd; empty Type = all
        try:
            payload = await self._post_asmx(
                self.paths().get_account_statements,
                {
                    "req": {
                        "CSCSNumber": account.external_account_id,
                        "ClientId": meta.get("client_id") or self._client_id,
                        "DateFrom": start,
                        "DateTo": end,
                        "Type": "",
                    }
                },
            )
            return parser.transactions_from_statements(payload)
        except AppError:
            return []

    async def _positions_for(self, cscs: str) -> dict[str, Any]:
        if cscs in self._positions_cache:
            return self._positions_cache[cscs]
        meta = await self._meta_for(cscs)
        payload = await self._post_asmx(
            self.paths().get_stock_positions,
            {
                "req": {
                    "CSCSNumber": cscs,
                    "ClientId": meta.get("client_id") or self._client_id,
                    "Market": "",
                    "Sector": "",
                }
            },
        )
        data = parser.stock_position_payload(payload)
        self._positions_cache[cscs] = data
        return data

    # --------------------------------------------------------------- deposits

    async def get_deposit_instructions(self) -> DepositInstructions:
        self._require_auth()
        if not self._account_meta:
            await self.get_accounts()
        cscs = next(iter(self._account_meta.keys()), None)
        if not cscs:
            raise AppError(ErrorCode.ACCOUNT_NOT_FOUND, f"No {self.provider_name} CSCS account found", status_code=404)
        payload = await self._post_asmx(self.paths().get_account_attributes, {"CSCSNumber": cscs})
        try:
            return parser.deposit_instructions_from_attributes(
                payload,
                funding_url=self.paths().deposit_page,
                brand=self.provider_name,
            )
        except LookupError as exc:
            raise AppError(
                ErrorCode.CONNECTOR_NOT_IMPLEMENTED,
                f"{self.provider_name} profile is missing account attributes for funding reference",
                status_code=501,
            ) from exc
        except ValueError as exc:
            raise AppError(
                ErrorCode.PORTFOLIO_EXTRACTION_FAILED,
                f"Could not parse {self.provider_name} funding details: {exc}",
                status_code=502,
            ) from exc

    # ---------------------------------------------------------- instruments

    async def list_markets(self) -> list[MarketInfo]:
        self._require_auth()
        return [MarketInfo(market_id=self.paths().default_market, name="Nigerian Exchange (NGX)", currency="NGN")]

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
            rows, market_id=market_id or self.paths().default_market, search=search, page=page, per_page=per_page
        )

    async def _all_securities(self) -> list[dict[str, Any]]:
        if self._securities is not None:
            return self._securities
        payload = await self._post_asmx(self.paths().get_securities, {"req": {}})
        self._securities = parser.securities_rows(payload)
        return self._securities

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
            raise AppError(ErrorCode.VALIDATION_ERROR, f"{self.provider_name} does not allow fractional shares", status_code=422)

        cscs, meta = await self._resolve_account(order.account_external_id)
        client_id = meta.get("client_id") or self._client_id
        if not client_id:
            raise AppError(ErrorCode.PORTFOLIO_EXTRACTION_FAILED, f"{self.provider_name} ClientId missing", status_code=502)

        sec = await self._security_by_symbol(symbol)
        if not sec:
            raise AppError(ErrorCode.VALIDATION_ERROR, f"Unknown {self.provider_name} symbol: {symbol}", status_code=422)

        is_buy = order.side.value.lower() == "buy"
        is_limit = order.order_type == OrderType.LIMIT
        if is_limit:
            price = float(order.limit_price)  # type: ignore[arg-type]
        else:
            last = parser.dec(sec.get("LastTradedPrice") or sec.get("ClosingPrice") or sec.get("OpeningPrice"))
            if last is None or last <= 0:
                raise AppError(ErrorCode.VALIDATION_ERROR, "Could not resolve a market price for this symbol", status_code=422)
            price = float(last)

        # Pre-validate like the portal quantity validator
        valid = await self._post_asmx(
            self.paths().is_mandate_valid,
            {
                "req": {
                    "AccountNumber": cscs,
                    "CSCSNumber": "",
                    "SecurityId": sec.get("SecurityID") or symbol,
                    "Quantity": str(int(order.quantity)),
                    "IsLimit": is_limit,
                    "Price": str(price),
                    "Yield": "",
                    "IsBuy": is_buy,
                    "ClientId": int(client_id),
                }
            },
        )
        vdata = parser.unwrap_d(valid)
        if isinstance(vdata, dict) and vdata.get("IsValid") is False:
            # Still attempt portal submit — portal also checks cash; surface clear funds error when known
            if is_buy:
                purchase = await self._post_asmx(self.paths().get_purchase_info, {"req": {"AccountNumber": cscs}})
                pdata = parser.unwrap_d(purchase)
                pi = pdata.get("PurchaseInfo") if isinstance(pdata, dict) else None
                if isinstance(pi, dict) and not pi.get("HasMoney"):
                    raise AppError(
                        ErrorCode.VALIDATION_ERROR,
                        f"{self.provider_name} available cash is insufficient to place this buy order",
                        status_code=422,
                        details={"oms_balance": pi.get("OMSBalance"), "cleared": pi.get("ClearedBalance")},
                    )
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                f"{self.provider_name} rejected this mandate (IsMandateValid=false)",
                status_code=422,
            )

        return await self._submit_eqty_mandate(
            side="buy" if is_buy else "sell",
            cscs=cscs,
            client_id=int(client_id),
            symbol=str(sec.get("SecurityID") or symbol),
            market=str(sec.get("Market") or "EQTY"),
            quantity=int(order.quantity),
            price=price,
            is_limit=is_limit,
        )

    async def _submit_eqty_mandate(
        self,
        *,
        side: str,
        cscs: str,
        client_id: int,
        symbol: str,
        market: str,
        quantity: int,
        price: float,
        is_limit: bool,
    ) -> OrderResult:
        url = self.paths().eqty_mandate(side)
        page = await self._raw("GET", url)
        html = page.text
        if "Login" in (re.search(r"<title>([^<]+)</title>", html, re.I).group(1) if re.search(r"<title>([^<]+)</title>", html, re.I) else ""):
            raise AppError(ErrorCode.INVALID_CREDENTIALS, f"{self.provider_name} session expired; re-authenticate", status_code=401)

        def gv(name: str) -> str:
            return _form_value(html, name)

        prefix = "ctl00$ctl00$ContentPlaceHolder1$content$OrdersBooking1$"
        order_type = "LIMIT" if is_limit else "MARKET"
        data: dict[str, str] = {
            "__VIEWSTATE": gv("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": gv("__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": gv("__EVENTVALIDATION"),
            "__EVENTTARGET": f"{prefix}btnsubmit",
            "__EVENTARGUMENT": "",
            f"{prefix}drpcscsnumbers": cscs,
            f"{prefix}Security1$txtsecurity": symbol,
            f"{prefix}Security1$hdfSecurityId": symbol,
            f"{prefix}Security1$hdfMarket": market or "EQTY",
            f"{prefix}txtquantity": str(quantity),
            f"{prefix}drpduration": gv(f"{prefix}drpduration") or "DAY",
            f"{prefix}drpordertype": order_type,
            f"{prefix}hdfordertype": order_type,
            f"{prefix}txtprice": f"{price:.2f}" if is_limit else "",
            f"{prefix}txtyield": "",
            f"{prefix}HdfSide": "BUY" if side == "buy" else "SELL",
            f"{prefix}drpexecinst": "",
            f"{prefix}drpqueue": "NO",
        }
        # Preserve antiforgery if present on page
        tok = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]*)"', html)
        if tok:
            data["__RequestVerificationToken"] = tok.group(1)

        resp = await self._raw(
            "POST",
            url,
            data=data,
            headers={"Referer": url, "Origin": self.paths().base},
        )
        body = resp.text
        title_m = re.search(r"<title>([^<]+)</title>", body, re.I)
        title = title_m.group(1).strip() if title_m else ""
        # Success heuristics observed on NaYa portals: confirmation text / order number
        order_id = None
        m_oid = re.search(r"Order\s*#?\s*[:\s]*([A-Za-z0-9\-]+)", body, re.I)
        if m_oid:
            order_id = m_oid.group(1)
        success_hints = ("success", "submitted", "accepted", "order number", "mandate")
        fail_hints = ("insufficient", "rejected", "error", "failed", "invalid", "login")
        lower = body.lower()
        failed = any(h in lower for h in fail_hints) and not order_id
        # If still on mandate form with validation errors
        if "New Buy Mandate" in title or "New Sell Mandate" in title:
            err = re.search(r'class="[^"]*error[^"]*"[^>]*>([^<]+)', body, re.I)
            msg = err.group(1).strip() if err and err.group(1).strip() else f"{self.provider_name} did not confirm order submission"
            # Insufficient funds commonly leaves user on the form
            if "insufficient" in lower or "balance" in lower:
                raise AppError(ErrorCode.VALIDATION_ERROR, msg, status_code=422)
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                msg,
                status_code=502,
                details={"page_title": title},
            )
        ok = bool(order_id) or any(h in lower for h in success_hints) or resp.status_code in (200, 302)
        if failed and not ok:
            raise AppError(ErrorCode.PROVIDER_UNAVAILABLE, f"{self.provider_name} order submission failed", status_code=502)
        return parser.order_result_from_mandate(
            side=side,
            symbol=symbol,
            success=True,
            message=f"Order submitted via {self.provider_name} EqtyMandate" if ok else "Submitted; confirmation text not found",
            order_id=order_id,
        )

    async def _security_by_symbol(self, symbol: str) -> dict[str, Any] | None:
        rows = await self._all_securities()
        symbol = symbol.upper()
        for row in rows:
            if str(row.get("SecurityID") or "").upper() == symbol:
                return row
            # also match long name loosely
            if str(row.get("Symbol") or "").upper() == symbol:
                return row
        return None

    async def _resolve_account(self, account_external_id: str | None) -> tuple[str, dict[str, Any]]:
        if not self._account_meta:
            await self.get_accounts()
        wanted = (account_external_id or "").strip()
        if wanted:
            meta = self._account_meta.get(wanted)
            if meta is None:
                raise AppError(ErrorCode.ACCOUNT_NOT_FOUND, f"Account not found on this {self.provider_name} connection", status_code=404)
            return wanted, meta
        if self._account_meta:
            cscs, meta = next(iter(self._account_meta.items()))
            return cscs, meta
        raise AppError(ErrorCode.ACCOUNT_NOT_FOUND, f"No {self.provider_name} CSCS account found", status_code=404)

    async def _meta_for(self, account_external_id: str) -> dict[str, Any]:
        if account_external_id not in self._account_meta:
            await self.get_accounts()
        meta = self._account_meta.get(account_external_id)
        if meta is None:
            raise AppError(ErrorCode.ACCOUNT_NOT_FOUND, f"Account not found on this {self.provider_name} connection", status_code=404)
        return meta

    # -------------------------------------------------------------- lifecycle

    async def test_connection(self) -> AuthResult:
        try:
            await self._ensure_client()
            resp = await self._raw("GET", self.paths().login)
            ok = resp.status_code < 500
            return AuthResult(
                success=ok,
                status="authenticated" if ok else "failed",
                message=f"{self.provider_name} portal reachable" if ok else f"{self.provider_name} returned HTTP {resp.status_code}",
            )
        except Exception:
            logger.exception("%s test_connection failed", self.provider_id)
            return AuthResult(success=False, status="failed", message=f"Unable to reach {self.provider_name}")
        finally:
            await self.disconnect()

    async def disconnect(self) -> None:
        self._authenticated = False
        self._cookies = {}
        self._account_meta = {}
        self._securities = None
        self._positions_cache = {}
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                logger.exception("Error closing %s HTTP client (ignored)", self.provider_id)
            finally:
                self._client = None

    # --------------------------------------------------------------- HTTP

    def _require_auth(self) -> None:
        if not self._authenticated or not self._cookies.get("__NAYA.AUTH"):
            raise AppError(ErrorCode.INVALID_CREDENTIALS, f"Not authenticated with {self.provider_name}", status_code=401)

    async def _ensure_client(self) -> None:
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=20.0),
            follow_redirects=True,
            headers={"User-Agent": _UA, "Accept": "text/html,application/json,*/*"},
            transport=self.transport,
        )
        # restore cookies if we already have them
        for k, v in self._cookies.items():
            self._client.cookies.set(k, v, domain=self.paths().cookie_domain)

    async def _raw(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        content: str | bytes | None = None,
    ) -> httpx.Response:
        if not _host_allowed(url, self.approved_domains | self.paths().approved_domains):
            raise AppError(ErrorCode.NAVIGATION_BLOCKED, "Request blocked: host not in provider allow-list", status_code=400)
        await self._ensure_client()
        assert self._client is not None
        # ensure cookies applied
        for k, v in self._cookies.items():
            self._client.cookies.set(k, v, domain=self.paths().cookie_domain)
        resp = await self._client.request(method, url, data=data, json=json, content=content, headers=headers)
        self._capture_cookies()
        return resp

    async def _asmx(self, url: str, payload: dict[str, Any]) -> httpx.Response:
        return await self._raw(
            "POST",
            url,
            content=json.dumps(payload),
            headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"},
        )

    async def _post_asmx(self, url: str, payload: dict[str, Any]) -> Any:
        resp = await self._asmx(url, payload)
        body = self._json(resp)
        if resp.status_code == 401:
            raise AppError(ErrorCode.INVALID_CREDENTIALS, f"{self.provider_name} session expired; re-authenticate", status_code=401)
        if resp.status_code >= 500:
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                parser.broker_message(body) or f"{self.provider_name} API returned HTTP {resp.status_code}",
                status_code=502,
            )
        if resp.status_code >= 400:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                parser.broker_message(body) or f"{self.provider_name} API returned HTTP {resp.status_code}",
                status_code=422,
                details={"broker_status": resp.status_code},
            )
        return body

    def _json(self, resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except Exception as exc:
            raise AppError(
                ErrorCode.PORTFOLIO_EXTRACTION_FAILED,
                f"{self.provider_name} API returned non-JSON payload",
                status_code=502,
            ) from exc
