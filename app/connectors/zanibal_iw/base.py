"""Base connector for Zanibal E-Business Suite (InvestWeb) portals.

Server-rendered ASP.NET MVC with a cookie session and anti-forgery tokens.
The connector is plain httpx + stdlib HTML parsing (no browser): it GETs the
login form, posts it with every hidden field (token names vary by site),
then reads the report pages. Subclasses set the site origin/base path and
whether the login password is AES-encrypted client-side (WSTC does this with
a fixed key embedded in its login page; SISL posts it as typed).
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, ClassVar
from urllib.parse import urljoin, urlparse

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
from app.core.errors import AppError, ErrorCode

from . import parser
from .html import Form, Page, parse_page

logger = logging.getLogger(__name__)

WAT = timezone(timedelta(hours=1))
SESSION_KEY = "api_session"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

COMMON_BLOCKERS = [
    "No API: data is scraped from server-rendered report pages (column lookup by header text).",
    "Order placement is a two-step form (Preview Order, then the confirm form on the preview page); "
    "the confirm step was not observed because the discovery account has no CSCS account linked.",
    "No dedicated funding account is shown in the portal, so deposit instructions are not offered.",
    "If the portal shows a Terms and Conditions prompt, sync stops (waiting_for_user) until you accept it on the website.",
]


def aes_cryptojs_encrypt(plaintext: str, key: str) -> str:
    """Match CryptoJS.AES.encrypt(Utf8(p), Utf8(key), {iv: Utf8(key), CBC, Pkcs7}).toString()."""
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    k = key.encode("utf-8")
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    enc = Cipher(algorithms.AES(k), modes.CBC(k)).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode("ascii")


class ZanibalIWConnector(BrokerConnector):
    """Subclass and set the ClassVars; register with @register_connector."""

    provider_id: ClassVar[str] = ""
    provider_name: ClassVar[str] = ""
    origin: ClassVar[str] = ""  # e.g. https://customerportal.wstc.com.ng
    base_path: ClassVar[str] = ""  # e.g. /iwnewebiz
    approved_domains: ClassVar[frozenset[str]] = frozenset()
    password_aes_key: ClassVar[str | None] = None  # set when the login page AES-encrypts the password
    username_label: ClassVar[str] = "Username"
    extra_blockers: ClassVar[list[str]] = []
    implementation_status: ClassVar[ImplementationStatus] = ImplementationStatus.SCAFFOLDED

    transport: httpx.AsyncBaseTransport | None = None  # tests inject MockTransport

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._authenticated = False
        self._customer_no: str | None = None
        self._pending_mfa: tuple[Form, str, str] | None = None  # (form, field, page_url)
        self._accounts: list[tuple[str, str]] | None = None

    # ------------------------------------------------------------ metadata

    @classmethod
    def metadata(cls) -> ProviderMetadata:
        return ProviderMetadata(
            provider_id=cls.provider_id,
            provider_name=cls.provider_name,
            connector_type=ConnectorType.AUTHENTICATED_HTTP,
            implementation_status=cls.implementation_status,
            auth_methods=[AuthMethod.USERNAME_PASSWORD],
            required_credential_fields=[
                CredentialField(name="username", label=cls.username_label, field_type="string", required=True,
                                secret=False, description=f"{cls.provider_name} portal login"),
                CredentialField(name="password", label="Password", field_type="password", required=True, secret=True),
            ],
            optional_credential_fields=[
                CredentialField(name="otp", label="OTP / token", field_type="otp", required=False, secret=True,
                                description="Only if the portal asks for a one-time code"),
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
            approved_domains=sorted(cls.approved_domains),
            notes=(
                f"Zanibal E-Business Suite portal at {cls.origin}{cls.base_path} (cookie session, HTML scraping). "
                "Equities (summary per stock), treasury bills, bonds, cash statement, portfolio transactions, "
                "market stats, equity buy/sell via the Trade form. Caveats: "
                + "; ".join(COMMON_BLOCKERS + list(cls.extra_blockers))
            ),
            operational=cls.implementation_status == ImplementationStatus.OPERATIONAL,
        )

    def url(self, path: str) -> str:
        return f"{self.origin}{self.base_path}{path}"

    # ------------------------------------------------------------------ auth

    async def authenticate(self, credentials: dict[str, Any], options: dict[str, Any] | None = None) -> AuthResult:
        options = options or {}
        username = (credentials.get("username") or "").strip()
        password = credentials.get("password") or ""
        otp = (credentials.get("otp") or "").strip()
        try:
            session = options.get(SESSION_KEY)
            if isinstance(session, dict) and session.get("cookies") and not otp:
                if await self._resume(session["cookies"]):
                    return self._auth_success("Authenticated via stored portal session", "session")
                await self._reset_client()

            if not username or not password:
                return AuthResult(success=False, status="failed", message="Username and password are required")

            resp, page = await self._get_page(self.url("/Home/Login"))
            form = next((f for f in page.forms if "UserName" in f.inputs and "Password" in f.inputs), None)
            if form is None:
                if parser.is_authenticated(page):
                    return self._auth_success("Already authenticated", "session")
                raise AppError(ErrorCode.PROVIDER_UNAVAILABLE,
                               f"{self.provider_name} login form not found; the portal may have changed", status_code=503)
            fields = form.fields()
            fields["UserName"] = username
            fields["Password"] = aes_cryptojs_encrypt(password, self.password_aes_key) if self.password_aes_key else password
            resp, page = await self._post_form(urljoin(str(resp.url), form.action or self.url("/Home/Login")), fields)
            return await self._classify_after_login(page, str(resp.url), otp)
        except AppError:
            raise
        except httpx.TimeoutException as exc:
            await self.disconnect()
            raise AppError(ErrorCode.AUTHENTICATION_TIMEOUT, f"{self.provider_name} login timed out", status_code=503) from exc
        except Exception as exc:
            logger.exception("%s authenticate failed", self.provider_id)
            await self.disconnect()
            raise AppError(ErrorCode.PROVIDER_UNAVAILABLE,
                           "Authentication attempt failed due to a provider or network error", status_code=503) from exc

    async def _classify_after_login(self, page: Page, page_url: str, otp: str) -> AuthResult:
        if parser.is_login_page(page):
            return AuthResult(success=False, status="failed",
                              message=parser.login_error(page) or "Authentication failed. Check credentials.")
        form, field = parser.mfa_form(page)
        if form is not None and not parser.is_authenticated(page):
            self._pending_mfa = (form, field, page_url)
            if otp:
                return await self.submit_mfa(otp)
            return AuthResult(success=False, status="mfa_required",
                              message="The portal asked for a one-time code. Submit it via /connections/{id}/mfa and re-sync.",
                              mfa_challenge={"type": "otp", "provider": self.provider_id})
        if parser.needs_terms(page):
            return AuthResult(success=False, status="waiting_for_user",
                              message="Accept the Terms and Conditions on the portal website, then re-sync.")
        if parser.is_authenticated(page):
            self._customer_no = parser.customer_number(page)
            return self._auth_success("Authenticated successfully", "password")
        return AuthResult(success=False, status="waiting_for_user", message="Unable to confirm portal login state")

    async def submit_mfa(self, code: str, challenge: dict[str, Any] | None = None) -> AuthResult:
        if not self._pending_mfa:
            return AuthResult(success=False, status="failed", message="No pending one-time-code prompt")
        form, field, page_url = self._pending_mfa
        fields = form.fields()
        fields[field] = code.strip()
        resp, page = await self._post_form(urljoin(page_url, form.action or page_url), fields)
        self._pending_mfa = None
        return await self._classify_after_login(page, str(resp.url), "")

    async def _resume(self, cookies: dict[str, str]) -> bool:
        client = await self._ensure_client()
        host = urlparse(self.origin).hostname or ""
        for k, v in cookies.items():
            client.cookies.set(k, v, domain=host)
        try:
            _, page = await self._get_page(self.url(""))
        except (httpx.HTTPError, AppError):
            return False
        if parser.is_authenticated(page):
            self._customer_no = parser.customer_number(page)
            return True
        return False

    def _auth_success(self, message: str, mode: str) -> AuthResult:
        self._authenticated = True
        cookies = {c.name: c.value for c in self._client.cookies.jar} if self._client else {}
        return AuthResult(
            success=True, status="authenticated", message=message,
            session_metadata={"auth_mode": mode},
            options_patch={SESSION_KEY: {"cookies": cookies, "saved_at": datetime.now(timezone.utc).isoformat()}}
            if cookies else None,
        )

    # -------------------------------------------------------------- portfolio

    async def _master_accounts(self) -> list[tuple[str, str]]:
        if self._accounts is None:
            _, page = await self._get_page(self.url("/CustInfo/AccountStatement"))
            self._accounts = parser.master_accounts(page) or [(self._customer_no or "main", "Account")]
            self._statement_page = page
        return self._accounts

    async def get_accounts(self) -> list[NormalizedAccount]:
        self._require_auth()
        accts = await self._master_accounts()
        cash = parser.statement_total(self._statement_page) if getattr(self, "_statement_page", None) else None
        acct_id, label = accts[0]
        return [NormalizedAccount(
            external_account_id=acct_id,
            account_name=f"{self.provider_name} {acct_id}",
            account_type="STOCKBROKING",
            currency="NGN",
            cash_balance=cash,
        )]

    async def get_holdings(self, account: NormalizedAccount) -> list[NormalizedHolding]:
        self._require_auth()
        _, summary = await self._get_page(self.url("/CustInfo/ChartSummaryPerStock"))
        _, holdings_page = await self._get_page(self.url("/CustInfo/PortfolioHoldings"))
        equities = parser.holdings_from_summary(summary)
        if not equities and not summary.table_with("Symbol", "Units", "Mkt. Value"):
            equities = parser.equities_from_holdings_page(holdings_page)
        return equities + parser.fixed_income_from_holdings_page(holdings_page)

    async def get_cash_balance(self, account: NormalizedAccount) -> Decimal | None:
        self._require_auth()
        _, page = await self._get_page(self.url("/CustInfo/AccountStatement"))
        total = parser.statement_total(page)
        if total is not None:
            return total
        _, trade = await self._get_page(self.url("/Trade/Trade"))
        return parser.trade_balances(trade)["cash"]

    async def get_portfolio_summary(self, account: NormalizedAccount) -> PortfolioSummary | None:
        self._require_auth()
        cash = await self.get_cash_balance(account)
        _, summary = await self._get_page(self.url("/CustInfo/ChartSummaryPerStock"))
        return parser.summary_totals(summary, cash=cash)

    async def get_transactions(self, account: NormalizedAccount) -> list[NormalizedTransaction]:
        self._require_auth()
        resp, page = await self._get_page(self.url("/CustInfo/AccountStatement"))
        cash: list[NormalizedTransaction] = []
        form = page.form_with_field("MasterAccount")
        if form is not None:
            end = datetime.now(WAT).date()
            fields = form.fields()
            fields.update({"Sortby": "0", "MasterAccount": account.external_account_id,
                           "StartDate": (end - timedelta(days=365)).isoformat(), "EndDate": end.isoformat()})
            _, stmt = await self._post_form(urljoin(str(resp.url), form.action or ""), fields)
            cash = parser.cash_transactions(stmt, account=account.external_account_id)
        _, sec = await self._get_page(self.url("/CustInfo/PortfolioTransactions"))
        return cash + parser.portfolio_transactions(sec, account=account.external_account_id)

    # ---------------------------------------------------------- instruments

    async def list_markets(self) -> list[MarketInfo]:
        self._require_auth()
        return [MarketInfo(market_id="NGX", name="Nigerian Exchange (NGX)", currency="NGN")]

    async def list_instruments(self, *, market_id: str | None = None, search: str | None = None,
                               page: int = 1, per_page: int = 100) -> InstrumentListResult:
        self._require_auth()
        if market_id and market_id.strip().upper() != "NGX":
            return InstrumentListResult(market_id=market_id, page=page, per_page=per_page, total=0, instruments=[])
        _, stats = await self._get_page(self.url("/Research/MarketStats"))
        return parser.instruments_from_market_stats(stats, search=search, page_no=page, per_page=per_page)

    # ----------------------------------------------------------------- orders

    async def place_order(self, order: OrderRequest) -> OrderResult:
        self._require_auth()
        symbol = (order.symbol or "").strip().upper()
        if not symbol:
            raise AppError(ErrorCode.VALIDATION_ERROR, "symbol is required", status_code=422)
        if order.order_type == OrderType.LIMIT and (order.limit_price is None or order.limit_price <= 0):
            raise AppError(ErrorCode.VALIDATION_ERROR, "limit_price is required for limit orders", status_code=422)

        resp, page = await self._get_page(self.url("/Trade/Trade"))
        form = page.form_with_field("Units")
        if form is None or "Symbol" not in form.selects:
            raise AppError(ErrorCode.PROVIDER_UNAVAILABLE, f"{self.provider_name} trade form not found", status_code=503)
        cscs = [v for v, _, _ in form.selects.get("CSCSAcctNumber", None).options if v] if "CSCSAcctNumber" in form.selects else []
        if not cscs:
            raise AppError(ErrorCode.CONNECTOR_NOT_IMPLEMENTED,
                           f"No CSCS account is linked on {self.provider_name}; trading is unavailable until one is set up",
                           status_code=501)
        symbols = {v.upper() for v, _, _ in form.selects["Symbol"].options if v}
        if symbol not in symbols:
            raise AppError(ErrorCode.VALIDATION_ERROR, f"Symbol '{symbol}' is not tradeable on {self.provider_name}", status_code=422)

        if order.quantity is not None and order.quantity > 0:
            if order.quantity != order.quantity.to_integral_value():
                raise AppError(ErrorCode.VALIDATION_ERROR, "Fractional shares are not allowed", status_code=422)
            units = int(order.quantity)
        elif order.amount is not None and order.amount > 0:
            price = order.limit_price if order.order_type == OrderType.LIMIT else await self._last_price(symbol)
            if not price:
                raise AppError(ErrorCode.PROVIDER_UNAVAILABLE, f"Could not resolve a price for {symbol}", status_code=502)
            units = parser.units_for_amount(order.amount, price)
            if units <= 0:
                raise AppError(ErrorCode.VALIDATION_ERROR, "amount is below the price of one share", status_code=422)
        else:
            raise AppError(ErrorCode.VALIDATION_ERROR, "quantity (shares) or amount (NGN) must be positive", status_code=422)

        wanted = (order.account_external_id or "").strip()
        fields = form.fields()
        fields.update({
            "Action": "Buy" if order.side == OrderSide.BUY else "Sell",
            "CSCSAcctNumber": wanted if wanted in cscs else cscs[0],
            "Symbol": symbol,
            "OrderType": "Limit" if order.order_type == OrderType.LIMIT else "Market",
            "PriceLimit": format(order.limit_price, "f") if order.order_type == OrderType.LIMIT else "0.0",
            "Units": str(units),
            # DateLimit is required by the form; keep the portal default (≈2 weeks) unless given
            "DateLimit": order.expires or fields.get("DateLimit", ""),
            "Instructions": (fields.get("Instructions") or "").strip() or "-",
        })
        # Step 1: Preview Order
        prev_resp, preview = await self._post_form(urljoin(str(resp.url), form.action or self.url("/Trade/Trade")), fields)
        err = self._form_error(preview)
        if err:
            raise AppError(ErrorCode.VALIDATION_ERROR, err, status_code=422)
        confirm = self._confirm_form(preview)
        if confirm is None:
            raise AppError(ErrorCode.PROVIDER_UNAVAILABLE,
                           f"{self.provider_name} did not return an order confirmation step; order not placed",
                           status_code=502)
        # Step 2: confirm exactly what the portal previewed (its hidden fields)
        done_resp, done = await self._post_form(urljoin(str(prev_resp.url), confirm.action or str(prev_resp.url)),
                                                confirm.fields())
        err = self._form_error(done)
        if err:
            raise AppError(ErrorCode.VALIDATION_ERROR, err, status_code=422)
        return OrderResult(
            external_order_id=None,
            status="submitted",
            side=order.side,
            symbol=symbol,
            message=(done.alerts[0] if done.alerts else "Order submitted"),
            raw={"units": units, "cscs": fields["CSCSAcctNumber"], "landing": urlparse(str(done_resp.url)).path},
        )

    @staticmethod
    def _form_error(page: Page) -> str | None:
        for a in page.alerts:
            low = a.lower()
            if any(k in low for k in ("insufficient", "error", "invalid", "not allowed", "failed", "unable", "cannot", "rejected")):
                return a
        return None

    @staticmethod
    def _confirm_form(page: Page) -> Form | None:
        for f in page.forms:
            labels = " ".join(f.submits).lower()
            if any(k in labels for k in ("confirm", "place order", "submit order", "proceed")):
                return f
        return None

    async def _last_price(self, symbol: str) -> Decimal | None:
        resp = await self._request("POST", self.url("/Trade/LastTrade"), data={"symbol": symbol},
                                   headers={"X-Requested-With": "XMLHttpRequest"})
        return parser.last_trade_price(resp.text)

    # -------------------------------------------------------------- lifecycle

    async def test_connection(self) -> AuthResult:
        try:
            _, page = await self._get_page(self.url("/Home/Login"))
            ok = parser.is_login_page(page)
            return AuthResult(success=ok, status="authenticated" if ok else "failed",
                              message="Login page reachable and form detected" if ok else "Login form not found")
        except Exception:
            logger.exception("%s test_connection failed", self.provider_id)
            return AuthResult(success=False, status="failed", message=f"Unable to reach {self.provider_name}")
        finally:
            await self.disconnect()

    async def disconnect(self) -> None:
        # No portal logout: keeping the cookie session lets the next sync resume without a fresh login.
        self._authenticated = False
        self._pending_mfa = None
        self._accounts = None
        await self._reset_client()

    # -------------------------------------------------------------- internals

    def _require_auth(self) -> None:
        if not self._authenticated or self._client is None:
            raise AppError(ErrorCode.INVALID_CREDENTIALS, f"Not authenticated with {self.provider_name}", status_code=401)

    def _host_allowed(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host == d or host.endswith("." + d) for d in self.approved_domains)

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(45.0),
                follow_redirects=False,
                transport=self.transport,
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
            )
        return self._client

    async def _reset_client(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                logger.exception("Error closing %s HTTP client (ignored)", self.provider_id)
            self._client = None

    async def _request(self, method: str, url: str, *, data: dict[str, str] | None = None,
                       headers: dict[str, str] | None = None) -> httpx.Response:
        """Follow redirects manually so every hop is checked against the allow-list."""
        client = await self._ensure_client()
        for _ in range(6):
            if not self._host_allowed(url):
                raise AppError(ErrorCode.NAVIGATION_BLOCKED, "Request blocked: host not in provider allow-list", status_code=400)
            resp = await client.request(method, url, data=data, headers=headers)
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                url = urljoin(url, resp.headers["location"])
                if resp.status_code in (301, 302, 303):
                    method, data = "GET", None
                continue
            if resp.status_code >= 500:
                raise AppError(ErrorCode.PROVIDER_UNAVAILABLE, f"{self.provider_name} returned HTTP {resp.status_code}", status_code=502)
            return resp
        raise AppError(ErrorCode.PROVIDER_UNAVAILABLE, f"{self.provider_name}: too many redirects", status_code=502)

    async def _get_page(self, url: str) -> tuple[httpx.Response, Page]:
        resp = await self._request("GET", url)
        page = parse_page(resp.text)
        if self._authenticated and parser.is_login_page(page):
            self._authenticated = False
            raise AppError(ErrorCode.INVALID_CREDENTIALS, f"{self.provider_name} session expired; re-authenticate", status_code=401)
        return resp, page

    async def _post_form(self, url: str, fields: dict[str, str]) -> tuple[httpx.Response, Page]:
        resp = await self._request("POST", url, data=fields, headers={"Content-Type": "application/x-www-form-urlencoded"})
        return resp, parse_page(resp.text)
