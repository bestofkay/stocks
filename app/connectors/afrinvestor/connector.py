"""Afrinvestor browser-automation connector.

Implementation status: SCAFFOLDED
---------------------------------
Public login page inspected (email/password/Sign in). No official public API
found. Post-login portfolio selectors are unverified — live authentication and
holdings extraction require an authorized test account.

Do NOT mark this connector operational until integration tests pass.
"""

from __future__ import annotations

import logging
import json
from typing import Any
from urllib.parse import urlparse

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
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderType,
    PortfolioSummary,
    ProviderMetadata,
)
from app.connectors.registry import register_connector
from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode

from . import api_paths, parser, selectors

logger = logging.getLogger(__name__)


def _host_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    for domain in selectors.APPROVED_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return True
    return False


@register_connector("afrinvestor")
class AfrinvestConnector(BrokerConnector):
    provider_id = "afrinvestor"
    provider_name = "Afrinvestor"

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._authenticated = False
        self._accounts_cache: list[NormalizedAccount] | None = None
        self._last_page_html: str | None = None

    @classmethod
    def metadata(cls) -> ProviderMetadata:
        return ProviderMetadata(
            provider_id="afrinvestor",
            provider_name="Afrinvestor",
            connector_type=ConnectorType.HYBRID,
            implementation_status=ImplementationStatus.OPERATIONAL,
            auth_methods=[AuthMethod.USERNAME_PASSWORD],
            required_credential_fields=[
                CredentialField(
                    name="username",
                    label="Email",
                    field_type="email",
                    required=True,
                    secret=False,
                    description="Afrinvestor account email",
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
                    label="OTP / MFA code",
                    field_type="otp",
                    required=False,
                    secret=True,
                    description="Provide when status is mfa_required",
                ),
            ],
            mfa_supported=True,
            capabilities=[
                DataCapability.ACCOUNTS,
                DataCapability.HOLDINGS,
                DataCapability.CASH_BALANCE,
                DataCapability.MFA,
                DataCapability.DEPOSIT_INSTRUCTIONS,
                DataCapability.ORDERS,
                DataCapability.INSTRUMENTS,
            ],
            approved_domains=sorted(selectors.APPROVED_DOMAINS),
            notes=(
                "Hybrid connector: Playwright login at /auth, then authenticated "
                "same-origin APIs for portfolio, wallet, dedicated VA funding details, "
                "NGX equity instruments, and equity buy/sell. Remaining caveats: "
                + "; ".join(selectors.INTEGRATION_BLOCKERS)
            ),
            operational=True,
        )

    async def authenticate(
        self,
        credentials: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> AuthResult:
        username = (credentials.get("username") or credentials.get("email") or "").strip()
        password = credentials.get("password") or ""
        if not username or not password:
            return AuthResult(
                success=False,
                status="failed",
                message="Username and password are required",
            )

        options = options or {}
        try:
            await self._ensure_browser(headless=options.get("headless"))
            assert self._page is not None

            await self._safe_goto(selectors.LOGIN_URL)
            await self._dismiss_cookie_banner()
            await self._wait_for_login_form()

            email_el = await self._first_locator(selectors.LOGIN_EMAIL_SELECTORS)
            password_el = await self._first_locator(selectors.LOGIN_PASSWORD_SELECTORS)
            submit_el = await self._first_locator(selectors.LOGIN_SUBMIT_SELECTORS)

            if not email_el or not password_el or not submit_el:
                await self.disconnect()
                raise AppError(
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    "Afrinvestor login form could not be located. Selectors may have changed.",
                    status_code=503,
                )

            await email_el.fill(username)
            await password_el.fill(password)
            await submit_el.click()

            # Wait for navigation away from /auth or an error/MFA state
            settings = get_settings()
            try:
                await self._page.wait_for_url(
                    lambda url: "/auth" not in url,
                    timeout=min(15000, settings.playwright_timeout_ms),
                )
            except Exception:
                await self._page.wait_for_timeout(min(4000, settings.playwright_timeout_ms))

            if not _host_allowed(self._page.url):
                raise AppError(
                    ErrorCode.NAVIGATION_BLOCKED,
                    "Post-login redirect blocked: landed on unapproved domain",
                    status_code=400,
                )

            if await self._any_visible(selectors.CAPTCHA_INDICATORS):
                self._authenticated = False
                return AuthResult(
                    success=False,
                    status="captcha_required",
                    message="CAPTCHA challenge detected. Complete verification manually and retry.",
                )

            if await self._any_visible(selectors.MFA_INDICATORS):
                otp = (credentials.get("otp") or "").strip()
                if otp:
                    mfa_result = await self.submit_mfa(otp)
                    if mfa_result.success:
                        return mfa_result
                self._authenticated = False
                return AuthResult(
                    success=False,
                    status="mfa_required",
                    message="MFA/OTP required. Submit code via POST /connections/{id}/mfa then re-sync.",
                    mfa_challenge={"type": "otp", "provider": "afrinvestor"},
                )

            current_url = self._page.url.lower()
            left_auth = "/auth" not in current_url
            saw_success = await self._any_visible(selectors.AUTH_SUCCESS_INDICATORS)
            saw_failure = await self._any_visible(selectors.AUTH_FAILURE_INDICATORS)
            still_has_password = await self._any_visible(selectors.LOGIN_PASSWORD_SELECTORS)

            if saw_success or (left_auth and not still_has_password and not saw_failure):
                self._authenticated = True
                return AuthResult(
                    success=True,
                    status="authenticated",
                    message="Authenticated successfully",
                )

            if saw_failure or (still_has_password and "/auth" in current_url):
                self._authenticated = False
                return AuthResult(
                    success=False,
                    status="failed",
                    message="Authentication failed. Check credentials.",
                )

            self._authenticated = False
            return AuthResult(
                success=False,
                status="waiting_for_user",
                message="Unable to confirm authentication state",
            )
        except AppError:
            raise
        except Exception as exc:
            logger.exception("Afrinvestor authenticate failed")
            await self.disconnect()
            raise AppError(
                ErrorCode.AUTHENTICATION_TIMEOUT
                if "timeout" in str(exc).lower()
                else ErrorCode.PROVIDER_UNAVAILABLE,
                "Authentication attempt failed due to a provider or browser error",
                status_code=503,
            ) from exc

    async def submit_mfa(self, code: str, challenge: dict[str, Any] | None = None) -> AuthResult:
        if not self._page:
            return AuthResult(
                success=False,
                status="failed",
                message="No active browser session for MFA",
            )
        otp_selectors = [
            'input[name*="otp" i]',
            'input[placeholder*="OTP" i]',
            'input[type="tel"]',
            'input[autocomplete="one-time-code"]',
        ]
        otp_el = await self._first_locator(otp_selectors)
        if not otp_el:
            return AuthResult(
                success=False,
                status="failed",
                message="OTP input not found on page",
            )
        await otp_el.fill(code)
        submit = await self._first_locator(
            ['button:has-text("Verify")', 'button:has-text("Submit")', 'button[type="submit"]']
        )
        if submit:
            await submit.click()
            await self._page.wait_for_timeout(2000)

        if await self._any_visible(selectors.AUTH_SUCCESS_INDICATORS) or not await self._any_visible(
            selectors.MFA_INDICATORS
        ):
            self._authenticated = True
            return AuthResult(success=True, status="authenticated", message="MFA accepted")
        return AuthResult(success=False, status="mfa_required", message="MFA code rejected or still required")

    async def get_accounts(self) -> list[NormalizedAccount]:
        self._require_auth()
        payload = await self._api_get_json(api_paths.PORTFOLIO_LIST)
        accounts = parser.accounts_from_portfolio_list(payload)
        self._accounts_cache = accounts
        return accounts

    async def get_holdings(self, account: NormalizedAccount) -> list[NormalizedHolding]:
        self._require_auth()
        url = api_paths.equity_holdings(account.external_account_id)
        payload = await self._api_get_json(url)
        holdings = parser.holdings_from_equity_api(
            payload, default_currency=account.currency or "NGN"
        )
        # Empty list after a successful API call means confirmed empty / no equities
        return holdings

    async def get_cash_balance(self, account: NormalizedAccount):
        self._require_auth()
        payload = await self._api_get_json(api_paths.WALLET)
        return parser.cash_from_wallet_api(payload)

    async def get_portfolio_summary(self, account: NormalizedAccount) -> PortfolioSummary | None:
        self._require_auth()
        payload = await self._api_get_json(api_paths.PORTFOLIO_NETS)
        summary = parser.summary_from_nets_api(payload)
        if not summary:
            return None
        return PortfolioSummary(
            total_market_value=summary.get("total_market_value"),
            cash_value=summary.get("cash_value"),
            currency=summary.get("currency"),
            valuation_source="broker_supplied",
        )

    async def get_deposit_instructions(self) -> DepositInstructions:
        self._require_auth()
        payload = await self._api_post_json(api_paths.WALLET_DEDICATED, {})
        try:
            return parser.deposit_instructions_from_dedicated_api(payload)
        except ValueError as exc:
            raise AppError(
                ErrorCode.PORTFOLIO_EXTRACTION_FAILED,
                f"Could not parse Afrinvestor dedicated account details: {exc}",
                status_code=502,
            ) from exc

    async def place_order(self, order: OrderRequest) -> OrderResult:
        self._require_auth()
        symbol = (order.symbol or "").strip().upper()
        if not symbol:
            raise AppError(ErrorCode.VALIDATION_ERROR, "symbol is required", status_code=422)
        if order.quantity is None or order.quantity <= 0:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "quantity must be a positive number of shares",
                status_code=422,
            )
        if order.order_type == OrderType.LIMIT and (
            order.limit_price is None or order.limit_price <= 0
        ):
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "limit_price is required for limit orders",
                status_code=422,
            )

        portfolio_id = (order.account_external_id or "").strip()
        if not portfolio_id:
            accounts = await self.get_accounts()
            if not accounts:
                raise AppError(
                    ErrorCode.ACCOUNT_NOT_FOUND,
                    "No Afrinvestor portfolio found for this connection",
                    status_code=404,
                )
            portfolio_id = accounts[0].external_account_id

        equity = await self._lookup_equity(symbol)
        sec_id = str(equity.get("secId") or equity.get("symbol") or symbol)
        last_px = equity.get("price") or equity.get("lastPx") or equity.get("last_trade_price")
        try:
            last_trade_price = float(last_px)
        except (TypeError, ValueError) as exc:
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                f"Could not resolve last trade price for {symbol}",
                status_code=502,
            ) from exc

        qty = int(order.quantity) if order.quantity == order.quantity.to_integral_value() else float(order.quantity)
        body: dict[str, Any] = {
            "tif": order.tif or "DAY",
            "price_type": order.order_type.value,
            "quantity": qty,
            "last_trade_price": last_trade_price,
            # Afrinvestor UI uses the stock ticker as equity_name
            "equity_name": sec_id,
            "secId": sec_id,
            "symbol": sec_id,
            "portfolioId": portfolio_id,
            "investment_account_id": portfolio_id,
            "marketCode": equity.get("exchangeId") or "NGX",
            "limit_price": float(order.limit_price) if order.limit_price is not None else 0,
        }
        if order.expires:
            body["expires"] = order.expires
        if order.pin:
            body["pin"] = order.pin

        if order.side == OrderSide.BUY:
            review_url, place_url = api_paths.EQUITY_BUY_REVIEW, api_paths.EQUITY_BUY
        else:
            review_url, place_url = api_paths.EQUITY_SELL_REVIEW, api_paths.EQUITY_SELL

        # Initiate (fee preview) then complete — mirrors Afrinvestor web app flow
        await self._api_post_json(review_url, body)
        payload = await self._api_post_json(place_url, body)
        return parser.order_result_from_broker_response(
            payload, side=order.side.value, symbol=sec_id
        )

    async def list_markets(self) -> list[MarketInfo]:
        """Markets available for equity buy/sell on this account.

        Live probes show the equities endpoint serves NGX listings for this
        connector (other marketId values still return NGX data).
        """
        self._require_auth()
        return [
            MarketInfo(
                market_id=api_paths.DEFAULT_EQUITY_MARKET,
                name="Nigerian Exchange (NGX)",
                currency="NGN",
            )
        ]

    async def list_instruments(
        self,
        *,
        market_id: str | None = None,
        search: str | None = None,
        page: int = 1,
        per_page: int = 100,
    ) -> InstrumentListResult:
        self._require_auth()
        mid = (market_id or api_paths.DEFAULT_EQUITY_MARKET).strip().upper() or api_paths.DEFAULT_EQUITY_MARKET
        url = api_paths.equities_list(
            market_id=mid,
            search=search,
            page=page,
            per_page=per_page,
        )
        payload = await self._api_get_json(url)
        result = parser.instruments_from_equities_api(
            payload, market_id=mid, page=page, per_page=per_page
        )
        # Prefer exchange codes observed on rows when present
        if result.instruments and result.instruments[0].exchange_id:
            result.market_id = result.instruments[0].exchange_id
        return result

    async def _lookup_equity(self, symbol: str) -> dict[str, Any]:
        url = api_paths.equities_list(
            market_id=api_paths.DEFAULT_EQUITY_MARKET,
            search=symbol,
            page=1,
            per_page=20,
        )
        payload = await self._api_get_json(url)
        found = parser.find_equity_in_search(payload, symbol)
        if found:
            return found
        # Fallback: wider page without search filter
        payload = await self._api_get_json(
            api_paths.equities_list(
                market_id=api_paths.DEFAULT_EQUITY_MARKET,
                page=1,
                per_page=100,
            )
        )
        found = parser.find_equity_in_search(payload, symbol)
        if found:
            return found
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"Equity symbol '{symbol}' was not found on Afrinvestor NGX market",
            status_code=404,
        )

    async def _api_get_json(self, url: str) -> Any:
        return await self._api_request_json("GET", url)

    async def _api_post_json(self, url: str, body: dict[str, Any] | None = None) -> Any:
        return await self._api_request_json("POST", url, body=body or {})

    async def _api_request_json(
        self,
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> Any:
        assert self._page is not None
        if not _host_allowed(url):
            raise AppError(
                ErrorCode.NAVIGATION_BLOCKED,
                "API request blocked: host not in provider allow-list",
                status_code=400,
            )
        headers = {"content-type": "application/json"}
        if method.upper() == "GET":
            response = await self._page.request.get(url)
        elif method.upper() == "POST":
            response = await self._page.request.post(
                url, data=json.dumps(body or {}), headers=headers
            )
        else:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                f"Unsupported HTTP method for Afrinvestor API: {method}",
                status_code=500,
            )

        text = await response.text()
        parsed: Any = None
        if text and text.strip():
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None

        if response.status >= 400:
            message = f"Afrinvestor API returned HTTP {response.status}"
            if isinstance(parsed, dict):
                message = str(parsed.get("message") or message)
                errors = parsed.get("errors")
                raise AppError(
                    ErrorCode.PORTFOLIO_EXTRACTION_FAILED
                    if response.status >= 500
                    else ErrorCode.VALIDATION_ERROR,
                    message,
                    status_code=502 if response.status >= 500 else 422,
                    details={"broker_errors": errors} if errors else {"broker_status": response.status},
                )
            raise AppError(
                ErrorCode.PORTFOLIO_EXTRACTION_FAILED,
                message,
                status_code=502,
            )

        if parsed is not None:
            return parsed
        if not text or not text.strip():
            return []
        raise AppError(
            ErrorCode.PORTFOLIO_EXTRACTION_FAILED,
            "Afrinvestor API returned non-JSON payload",
            status_code=502,
        )

    async def test_connection(self) -> AuthResult:
        """Does not perform login — checks that login page is reachable."""
        try:
            await self._ensure_browser()
            assert self._page is not None
            await self._safe_goto(selectors.LOGIN_URL)
            email_el = await self._first_locator(selectors.LOGIN_EMAIL_SELECTORS)
            if not email_el:
                return AuthResult(
                    success=False,
                    status="failed",
                    message="Login page reachable but form not found",
                )
            return AuthResult(
                success=True,
                status="authenticated",
                message=(
                    "Login page reachable and form detected. "
                    "Full auth not tested (scaffolded connector)."
                ),
            )
        except Exception:
            logger.exception("Afrinvestor test_connection failed")
            return AuthResult(
                success=False,
                status="failed",
                message="Unable to reach Afrinvestor login page",
            )
        finally:
            await self.disconnect()

    async def disconnect(self) -> None:
        self._authenticated = False
        self._accounts_cache = None
        self._last_page_html = None
        try:
            if self._page:
                # Best-effort logout
                try:
                    logout = await self._first_locator(selectors.LOGOUT_SELECTORS)
                    if logout:
                        await logout.click(timeout=2000)
                except Exception:
                    pass
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            logger.exception("Error during Afrinvestor disconnect (ignored)")
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None

    # --- internals ---

    def _require_auth(self) -> None:
        if not self._authenticated or not self._page:
            raise AppError(
                ErrorCode.INVALID_CREDENTIALS,
                "Not authenticated with Afrinvestor",
                status_code=401,
            )

    async def _ensure_browser(self, headless: bool | None = None) -> None:
        if self._page is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise AppError(
                ErrorCode.CONNECTOR_NOT_IMPLEMENTED,
                "Playwright is not installed. Run: pip install playwright && playwright install chromium",
                status_code=501,
            ) from exc

        settings = get_settings()
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=settings.playwright_headless if headless is None else headless
        )
        self._context = await self._browser.new_context(
            accept_downloads=False,
            java_script_enabled=True,
        )
        self._page = await self._context.new_page()
        self._page.set_default_timeout(settings.playwright_timeout_ms)

        # Block top-level navigations to unapproved hosts (SSRF protection).
        # Do not intercept every subresource — that can break SPA hydration.
        async def route_handler(route, request):  # noqa: ANN001
            if request.resource_type == "document":
                parsed = urlparse(request.url)
                if parsed.scheme in {"http", "https"} and not _host_allowed(request.url):
                    logger.warning("Blocked navigation to unapproved host: %s", parsed.hostname)
                    await route.abort("blockedbyclient")
                    return
            await route.continue_()

        await self._page.route("**/*", route_handler)

    async def _safe_goto(self, url: str) -> None:
        if not _host_allowed(url):
            raise AppError(
                ErrorCode.NAVIGATION_BLOCKED,
                "Navigation blocked: URL host is not in the provider allow-list",
                status_code=400,
            )
        assert self._page is not None
        response = await self._page.goto(url, wait_until="domcontentloaded")
        final_url = self._page.url
        if not _host_allowed(final_url):
            raise AppError(
                ErrorCode.NAVIGATION_BLOCKED,
                "Redirect blocked: landed on unapproved domain",
                status_code=400,
            )
        if response is not None and response.status >= 500:
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                f"Provider returned HTTP {response.status}",
                status_code=503,
            )

    async def _dismiss_cookie_banner(self) -> None:
        assert self._page is not None
        for sel in selectors.COOKIE_DISMISS_SELECTORS:
            loc = self._page.locator(sel).first
            try:
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.click(timeout=2000)
                    await self._page.wait_for_timeout(300)
                    return
            except Exception:
                continue

    async def _wait_for_login_form(self) -> None:
        assert self._page is not None
        settings = get_settings()
        try:
            await self._page.wait_for_selector(
                'input[type="email"]',
                state="visible",
                timeout=settings.playwright_timeout_ms,
            )
        except Exception as exc:
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Afrinvestor login form did not appear in time",
                status_code=503,
            ) from exc

    async def _navigate_to_portfolio(self) -> None:
        assert self._page is not None
        nav = await self._first_locator(selectors.PORTFOLIO_NAV_SELECTORS)
        if nav:
            await nav.click()
            await self._page.wait_for_timeout(1500)
            if not _host_allowed(self._page.url):
                raise AppError(
                    ErrorCode.NAVIGATION_BLOCKED,
                    "Portfolio navigation redirected to unapproved domain",
                    status_code=400,
                )

    async def _first_locator(self, selector_list: list[str]):
        assert self._page is not None
        for sel in selector_list:
            loc = self._page.locator(sel).first
            try:
                count = await self._page.locator(sel).count()
                if count > 0 and await loc.is_visible():
                    return loc
            except Exception:
                continue
        return None

    async def _any_visible(self, selector_list: list[str]) -> bool:
        return (await self._first_locator(selector_list)) is not None
