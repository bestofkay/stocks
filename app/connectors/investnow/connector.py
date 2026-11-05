"""InvestNow (United Capital) browser-driven connector.

Implementation status: SCAFFOLDED (pages and API responses observed 2026-11-03)
-------------------------------------------------------------------------------
Login fills the real two-step form in Playwright (the site encrypts the fields
itself). Data comes from JSON responses the app loads while the connector opens
/portfolio and /dashboard. Imperva challenges are reported, never bypassed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

from app.connectors.base import BrokerConnector
from app.connectors.models import (
    AuthMethod,
    AuthResult,
    ConnectorType,
    CredentialField,
    DataCapability,
    ImplementationStatus,
    NormalizedAccount,
    NormalizedHolding,
    NormalizedTransaction,
    PortfolioSummary,
    ProviderMetadata,
)
from app.connectors.registry import register_connector
from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode

from . import parser, selectors

logger = logging.getLogger(__name__)


def _host_allowed(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in selectors.APPROVED_DOMAINS


@register_connector("investnow")
class InvestNowConnector(BrokerConnector):
    provider_id = "investnow"
    provider_name = "InvestNow (United Capital)"

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._authenticated = False
        self._captured: dict[str, Any] = {}

    @classmethod
    def metadata(cls) -> ProviderMetadata:
        return ProviderMetadata(
            provider_id="investnow",
            provider_name="InvestNow (United Capital)",
            connector_type=ConnectorType.BROWSER_AUTOMATION,
            implementation_status=ImplementationStatus.SCAFFOLDED,
            auth_methods=[AuthMethod.USERNAME_PASSWORD],
            required_credential_fields=[
                CredentialField(name="username", label="Email", field_type="email", required=True, secret=False),
                CredentialField(name="password", label="Password", field_type="password", required=True, secret=True),
            ],
            mfa_supported=False,
            capabilities=[
                DataCapability.ACCOUNTS,
                DataCapability.HOLDINGS,
                DataCapability.CASH_BALANCE,
                DataCapability.TRANSACTIONS,
                DataCapability.PORTFOLIO_SUMMARY,
            ],
            approved_domains=sorted(selectors.APPROVED_DOMAINS),
            notes="Playwright login on app.investnow.ng; reads the JSON the app itself loads. Caveats: "
            + "; ".join(selectors.INTEGRATION_BLOCKERS),
            operational=False,
        )

    # ------------------------------------------------------------------ auth

    async def authenticate(self, credentials: dict[str, Any], options: dict[str, Any] | None = None) -> AuthResult:
        options = options or {}
        email = (credentials.get("username") or credentials.get("email") or "").strip()
        password = credentials.get("password") or ""
        if not email or not password:
            return AuthResult(success=False, status="failed", message="Email and password are required")
        try:
            await self._ensure_browser(headless=options.get("headless"))
            assert self._page is not None
            await self._goto(selectors.LOGIN_URL)
            if await self._bot_challenge():
                return self._captcha()
            email_el = await self._first(selectors.EMAIL_SELECTORS)
            if not email_el:
                raise AppError(ErrorCode.PROVIDER_UNAVAILABLE, "InvestNow login form not found", status_code=503)
            await email_el.fill(email)
            await (await self._first(selectors.CONTINUE_SELECTORS)).click()
            pw = await self._wait_first(selectors.PASSWORD_SELECTORS, timeout_ms=15000)
            if not pw:
                return AuthResult(success=False, status="failed", message=await self._page_error() or "Email not recognised")
            await pw.fill(password)
            await (await self._first(selectors.LOGIN_SUBMIT_SELECTORS)).click()
            try:
                await self._page.wait_for_url(lambda u: any(p in u for p in selectors.AUTH_SUCCESS_URL_PARTS), timeout=20000)
            except Exception:
                pass
            if await self._bot_challenge():
                return self._captcha()
            if await self._first(selectors.OTP_SELECTORS):
                return AuthResult(success=False, status="waiting_for_user",
                                  message="InvestNow asked for a one-time code; complete it on the website then retry.")
            if any(p in self._page.url for p in selectors.AUTH_SUCCESS_URL_PARTS):
                self._authenticated = True
                return AuthResult(success=True, status="authenticated", message="Authenticated successfully",
                                  session_metadata={"auth_mode": "browser"})
            return AuthResult(success=False, status="failed",
                              message=await self._page_error() or "Authentication failed. Check credentials.")
        except AppError:
            await self.disconnect()
            raise
        except Exception as exc:
            logger.exception("InvestNow authenticate failed")
            await self.disconnect()
            raise AppError(ErrorCode.AUTHENTICATION_TIMEOUT if "timeout" in str(exc).lower()
                           else ErrorCode.PROVIDER_UNAVAILABLE,
                           "InvestNow authentication failed due to a provider or browser error", status_code=503) from exc

    def _captcha(self) -> AuthResult:
        self._authenticated = False
        return AuthResult(success=False, status="captcha_required",
                          message="InvestNow's bot protection challenged the browser. Retry with headless=false "
                                  "(and complete any check yourself) or later.")

    # -------------------------------------------------------------- data

    async def _collect(self) -> dict[str, Any]:
        """Open /portfolio and /dashboard and keep the JSON the app loads from api.investnow.ng."""
        if self._captured:
            return self._captured
        self._require_auth()
        assert self._page is not None
        wanted = {selectors.PATH_PORTFOLIO_ITEMS, selectors.PATH_CUSTOMER, selectors.PATH_INVOICES,
                  selectors.PATH_WITHDRAWALS}
        got: dict[str, Any] = {}

        async def on_response(resp):  # noqa: ANN001
            try:
                u = urlparse(resp.url)
                if u.hostname == selectors.API_HOST and u.path in wanted and resp.status < 400:
                    got[u.path] = await resp.json()
            except Exception:
                pass

        self._page.on("response", on_response)
        try:
            for url in (selectors.PORTFOLIO_URL, selectors.DASHBOARD_URL):
                await self._goto(url)
                await self._page.wait_for_timeout(4000)
                if "/login" in self._page.url:
                    self._authenticated = False
                    raise AppError(ErrorCode.INVALID_CREDENTIALS, "InvestNow session ended; re-authenticate", status_code=401)
        finally:
            self._page.remove_listener("response", on_response)
        if selectors.PATH_PORTFOLIO_ITEMS not in got:
            raise AppError(ErrorCode.PORTFOLIO_EXTRACTION_FAILED,
                           "InvestNow portfolio data was not loaded by the app", status_code=502)
        self._captured = got
        return got

    async def _entries(self) -> list[dict[str, Any]]:
        return parser.portfolio_entries((await self._collect()).get(selectors.PATH_PORTFOLIO_ITEMS))

    async def get_accounts(self) -> list[NormalizedAccount]:
        return parser.accounts(await self._entries())

    async def get_holdings(self, account: NormalizedAccount) -> list[NormalizedHolding]:
        return parser.holdings_for(await self._entries(), account.external_account_id)

    async def get_cash_balance(self, account: NormalizedAccount):
        acct = next((a for a in parser.accounts(await self._entries())
                     if a.external_account_id == account.external_account_id), None)
        return acct.cash_balance if acct else None

    async def get_portfolio_summary(self, account: NormalizedAccount) -> PortfolioSummary | None:
        return parser.summary_for(await self._entries(), account.external_account_id)

    async def get_transactions(self, account: NormalizedAccount) -> list[NormalizedTransaction]:
        c = await self._collect()
        return parser.transactions(c.get(selectors.PATH_INVOICES), c.get(selectors.PATH_WITHDRAWALS))

    async def test_connection(self) -> AuthResult:
        try:
            await self._ensure_browser()
            await self._goto(selectors.LOGIN_URL)
            if await self._bot_challenge():
                return self._captcha()
            ok = await self._first(selectors.EMAIL_SELECTORS) is not None
            return AuthResult(success=ok, status="authenticated" if ok else "failed",
                              message="Login page reachable and form detected" if ok else "Login form not found")
        except Exception:
            logger.exception("InvestNow test_connection failed")
            return AuthResult(success=False, status="failed", message="Unable to reach InvestNow")
        finally:
            await self.disconnect()

    async def disconnect(self) -> None:
        self._authenticated = False
        self._captured = {}
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            logger.exception("Error during InvestNow disconnect (ignored)")
        finally:
            self._page = self._context = self._browser = self._playwright = None

    # -------------------------------------------------------------- internals

    def _require_auth(self) -> None:
        if not self._authenticated or not self._page:
            raise AppError(ErrorCode.INVALID_CREDENTIALS, "Not authenticated with InvestNow", status_code=401)

    async def _ensure_browser(self, headless: bool | None = None) -> None:
        if self._page is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise AppError(ErrorCode.CONNECTOR_NOT_IMPLEMENTED,
                           "Playwright is not installed. Run: pip install playwright && playwright install chromium",
                           status_code=501) from exc
        settings = get_settings()
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=settings.playwright_headless if headless is None else headless)
        self._context = await self._browser.new_context(accept_downloads=False)
        self._page = await self._context.new_page()
        self._page.set_default_timeout(settings.playwright_timeout_ms)

        async def route_handler(route, request):  # noqa: ANN001 - block navigations off the allow-list
            if request.resource_type == "document" and urlparse(request.url).scheme in {"http", "https"} \
                    and not _host_allowed(request.url):
                await route.abort("blockedbyclient")
                return
            await route.continue_()

        await self._page.route("**/*", route_handler)

    async def _goto(self, url: str) -> None:
        if not _host_allowed(url):
            raise AppError(ErrorCode.NAVIGATION_BLOCKED, "Navigation blocked: host not in allow-list", status_code=400)
        assert self._page is not None
        resp = await self._page.goto(url, wait_until="domcontentloaded")
        if not _host_allowed(self._page.url):
            raise AppError(ErrorCode.NAVIGATION_BLOCKED, "Redirect blocked: landed on unapproved domain", status_code=400)
        if resp is not None and resp.status >= 500:
            raise AppError(ErrorCode.PROVIDER_UNAVAILABLE, f"InvestNow returned HTTP {resp.status}", status_code=503)

    async def _first(self, sels: list[str]):
        assert self._page is not None
        for sel in sels:
            loc = self._page.locator(sel).first
            try:
                if await loc.count() > 0 and await loc.is_visible():
                    return loc
            except Exception:
                continue
        return None

    async def _wait_first(self, sels: list[str], *, timeout_ms: int):
        waited = 0
        while waited < timeout_ms:
            el = await self._first(sels)
            if el:
                return el
            await asyncio.sleep(0.5)
            waited += 500
        return None

    async def _page_text(self) -> str:
        try:
            return await self._page.inner_text("body")
        except Exception:
            return ""

    async def _bot_challenge(self) -> bool:
        text = (await self._page_text()).lower()
        return any(t.lower() in text for t in selectors.BOT_CHALLENGE_TEXT)

    async def _page_error(self) -> str | None:
        text = await self._page_text()
        for line in text.splitlines():
            if any(t.lower() in line.lower() for t in selectors.AUTH_FAILURE_TEXT):
                return line.strip()[:200]
        return None
