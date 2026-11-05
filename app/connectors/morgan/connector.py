"""Morgan Capital modern-app connector (morgancapitalgroup.com).

Implementation status: OPERATIONAL (live-verified 2026-10-29)
-------------------------------------------------------------
Auth lifecycle (production):
  1. Reuse encrypted Playwright ``storage_state`` in headless scrape mode.
  2. On miss/expiry, SeleniumBase UC bootstrap (Linux: Xvfb; never Turnstile
     headless) → persist ``storage_state`` via ``AuthResult.options_patch``.
  3. CDP Chrome fallback if UC is unavailable.

Portfolio / wallet / markets: scrape authenticated HTML (no REST ``/api/*``;
those routes 404). Virtual-account numbers and equity orders require KYC
(observed locked until KYC is approved).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
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
    PortfolioSummary,
    ProviderMetadata,
)
from app.connectors.registry import register_connector
from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode

from . import api_paths, parser, selectors, session as morgan_session

logger = logging.getLogger(__name__)


def _host_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    for domain in selectors.APPROVED_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return True
    return False


def _find_chrome() -> str | None:
    for c in (
        os.environ.get("CHROME_PATH", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        shutil.which("chrome") or "",
    ):
        if c and os.path.isfile(c):
            return c
    return None


@register_connector("morgan")
class MorganCapitalConnector(BrokerConnector):
    provider_id = "morgan"
    provider_name = "Morgan Capital"

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._authenticated = False
        self._chrome_proc: subprocess.Popen | None = None
        self._chrome_profile: str | None = None
        self._accounts_cache: list[NormalizedAccount] | None = None
        self._page_cache: dict[str, str] = {}

    @classmethod
    def metadata(cls) -> ProviderMetadata:
        return ProviderMetadata(
            provider_id="morgan",
            provider_name="Morgan Capital",
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
                    description="Morgan Capital modern-app account email",
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
            approved_domains=sorted(
                d for d in selectors.APPROVED_DOMAINS if "cloudflare" not in d
            ),
            notes=(
                "Hybrid connector for Morgan Capital modern web app. "
                "UC Turnstile bootstrap (Linux Xvfb) persists encrypted "
                "storage_state; subsequent work reuses headless Playwright. "
                "Remaining caveats: "
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
                message="Email and password are required",
            )
        options = options or {}
        scrape_headless = morgan_session.scrape_headless_default(options)
        try:
            # 1) Reuse encrypted / caller-supplied storage_state (headless scrape)
            storage_state = options.get("storage_state")
            if isinstance(storage_state, (dict, str)) and storage_state:
                ok = await self._try_storage_state(storage_state, scrape_headless)
                if ok:
                    refreshed = await self._export_storage_state()
                    patch_state = refreshed or (
                        storage_state if isinstance(storage_state, dict) else None
                    )
                    return AuthResult(
                        success=True,
                        status="authenticated",
                        message="Authenticated via stored browser session",
                        session_metadata={
                            "auth_mode": "storage_state",
                            "scrape_headless": scrape_headless,
                        },
                        options_patch=(
                            {"storage_state": patch_state} if patch_state else None
                        ),
                    )
                await self.disconnect()
                logger.info("Morgan storage_state expired; bootstrapping UC session")

            # 2) SeleniumBase UC Mode — Turnstile bootstrap (Xvfb on Linux)
            prefer_uc = str(options.get("auth_mode") or "uc").strip().lower() != "cdp"
            if prefer_uc:
                uc_result = await self._authenticate_uc(username, password, options)
                if uc_result.success or uc_result.status != "unavailable":
                    return uc_result

            # 3) CDP Chrome fallback
            return await self._authenticate_cdp(username, password, options)
        except AppError:
            raise
        except Exception as exc:
            logger.exception("Morgan authenticate failed")
            await self.disconnect()
            raise AppError(
                ErrorCode.AUTHENTICATION_TIMEOUT
                if "timeout" in str(exc).lower()
                else ErrorCode.PROVIDER_UNAVAILABLE,
                "Morgan authentication failed due to a provider or browser error",
                status_code=503,
            ) from exc

    async def _try_storage_state(
        self,
        storage_state: dict[str, Any] | str,
        headless: bool | None,
    ) -> bool:
        await self._ensure_browser(headless=headless, storage_state=storage_state)
        assert self._page is not None
        await self._safe_goto(api_paths.MODERN_DASHBOARD)
        await self._page.wait_for_timeout(2000)
        if "/sign-in" not in self._page.url and await self._any_visible(
            selectors.AUTH_SUCCESS_INDICATORS
        ):
            self._authenticated = True
            return True
        return False

    async def _export_storage_state(self) -> dict[str, Any] | None:
        if self._context is None:
            return None
        try:
            return await self._context.storage_state()
        except Exception:
            logger.exception("Could not export Morgan Playwright storage_state")
            return None

    async def _authenticate_uc(
        self,
        username: str,
        password: str,
        options: dict[str, Any],
    ) -> AuthResult:
        """UC Turnstile login → storage_state → headless Playwright handoff."""
        wait_sec = int(options.get("captcha_timeout_sec") or 180)
        # Login display: never default to Chrome headless (breaks Turnstile).
        login_headless = options.get("uc_headless")
        login_headed = options.get("uc_headed")
        scrape_headless = morgan_session.scrape_headless_default(options)

        try:
            result = await asyncio.to_thread(
                morgan_session.uc_login,
                username,
                password,
                captcha_timeout_sec=wait_sec,
                headless=bool(login_headless) if login_headless is not None else None,
                headed=bool(login_headed) if login_headed is not None else None,
            )
        except Exception as exc:
            logger.exception("Morgan UC login failed")
            return AuthResult(
                success=False,
                status="unavailable",
                message=f"UC login error: {exc}",
            )

        if not result.get("ok"):
            status = str(result.get("status") or "failed")
            return AuthResult(
                success=False,
                status=status,
                message=str(result.get("detail") or "SeleniumBase UC login did not complete"),
                session_metadata={"auth_mode": "uc", "notes": result.get("notes")},
            )

        storage = result["storage_state"]
        await self._ensure_browser(headless=scrape_headless, storage_state=storage)
        assert self._page is not None
        await self._safe_goto(api_paths.MODERN_DASHBOARD)
        await self._page.wait_for_timeout(2000)
        if "/sign-in" in self._page.url:
            self._authenticated = False
            return AuthResult(
                success=False,
                status="failed",
                message="UC session cookies did not open the dashboard in Playwright",
            )
        self._authenticated = True
        refreshed = await self._export_storage_state()
        return AuthResult(
            success=True,
            status="authenticated",
            message="Authenticated with Morgan Capital via SeleniumBase UC",
            session_metadata={
                "auth_mode": "uc",
                "scrape_headless": scrape_headless,
                "notes": result.get("notes"),
            },
            options_patch={"storage_state": refreshed or storage},
        )

    async def _authenticate_cdp(
        self,
        username: str,
        password: str,
        options: dict[str, Any],
    ) -> AuthResult:
        chrome = _find_chrome()
        if not chrome:
            return await self._authenticate_playwright_channel(username, password, options)

        port = int(options.get("cdp_port") or 9335)
        wait_sec = int(options.get("captcha_timeout_sec") or 180)
        profile = tempfile.mkdtemp(prefix="morgan_pw_")
        self._chrome_profile = profile
        self._chrome_proc = subprocess.Popen(
            [
                chrome,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                api_paths.MODERN_SIGN_IN,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await asyncio.sleep(3)

        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        browser = None
        for _ in range(25):
            try:
                browser = await self._playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{port}"
                )
                break
            except Exception:
                await asyncio.sleep(0.8)
        if browser is None:
            await self.disconnect()
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Could not attach to Chrome for Morgan login",
                status_code=503,
            )
        self._browser = browser
        self._context = browser.contexts[0]
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        page = self._page
        settings = get_settings()
        page.set_default_timeout(settings.playwright_timeout_ms)

        if "sign-in" not in page.url:
            await page.goto(api_paths.MODERN_SIGN_IN, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        await page.locator('input[type="email"]').fill(username)
        await page.locator('input[type="password"]').fill(password)
        try:
            await page.wait_for_function("() => typeof turnstile !== 'undefined'", timeout=30000)
        except Exception as exc:
            await self.disconnect()
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Morgan Turnstile library did not load",
                status_code=503,
            ) from exc

        await page.evaluate(
            """(sitekey) => {
              const host = document.querySelector('.mt-4.flex.justify-center');
              if (!host || typeof turnstile === 'undefined') return;
              host.innerHTML = '';
              const mount = document.createElement('div');
              host.appendChild(mount);
              const inp = document.createElement('input');
              inp.type = 'hidden'; inp.name = 'cf-turnstile-response';
              host.appendChild(inp);
              window.__morganTsToken = '';
              turnstile.render(mount, {
                sitekey, appearance: 'always', size: 'normal', theme: 'light',
                callback: (t) => { window.__morganTsToken = t; inp.value = t; }
              });
            }""",
            api_paths.TURNSTILE_SITEKEY,
        )

        token = ""
        deadline = time.monotonic() + wait_sec
        while time.monotonic() < deadline:
            for fr in page.frames:
                if "challenge" not in fr.url and "turnstile" not in fr.url:
                    continue
                for sel in ('input[type="checkbox"]', '[role="checkbox"]', "label"):
                    try:
                        await fr.locator(sel).first.click(timeout=500, force=True)
                        break
                    except Exception:
                        continue
            token = await page.evaluate(
                """() => window.__morganTsToken ||
                  (turnstile.getResponse && turnstile.getResponse()) ||
                  (document.querySelector('input[name=cf-turnstile-response]')||{}).value || ''"""
            )
            if token and len(token) > 20:
                break
            await page.wait_for_timeout(1500)

        if not token or len(token) < 20:
            self._authenticated = False
            return AuthResult(
                success=False,
                status="captcha_required",
                message=(
                    "Cloudflare Turnstile did not complete. Leave the Chrome window open, "
                    "click Verify you are human if shown, then retry — or pass "
                    "options.storage_state from a manual login."
                ),
            )

        result = await page.evaluate(
            """async ({ email, password, token, actionId }) => {
              const res = await fetch('/sign-in', {
                method: 'POST',
                headers: {
                  'Next-Action': actionId,
                  'Accept': 'text/x-component',
                  'Content-Type': 'text/plain;charset=UTF-8',
                },
                body: JSON.stringify([email, password, token]),
                credentials: 'same-origin',
              });
              return { status: res.status, text: (await res.text()).slice(0, 1500) };
            }""",
            {
                "email": username,
                "password": password,
                "token": token,
                "actionId": api_paths.SIGN_IN_ACTION_ID,
            },
        )
        text = (result or {}).get("text") or ""
        if "captcha" in text.lower() and "disallowed" in text.lower():
            self._authenticated = False
            return AuthResult(
                success=False,
                status="captcha_required",
                message="Morgan rejected the captcha token",
            )
        if '"ok":false' in text.replace(" ", ""):
            self._authenticated = False
            return AuthResult(success=False, status="failed", message="Authentication failed")

        await page.goto(api_paths.MODERN_DASHBOARD, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        if "/sign-in" in page.url:
            self._authenticated = False
            return AuthResult(
                success=False,
                status="failed",
                message="Login did not establish a dashboard session",
            )

        self._authenticated = True
        refreshed = await self._export_storage_state()
        return AuthResult(
            success=True,
            status="authenticated",
            message="Authenticated with Morgan Capital (modern app)",
            session_metadata={"auth_mode": "cdp"},
            options_patch={"storage_state": refreshed} if refreshed else None,
        )

    async def _authenticate_playwright_channel(
        self,
        username: str,
        password: str,
        options: dict[str, Any],
    ) -> AuthResult:
        await self._ensure_browser(headless=False if options.get("headless") is None else options.get("headless"))
        assert self._page is not None
        await self._safe_goto(api_paths.MODERN_SIGN_IN)
        await self._page.locator('input[type="email"]').fill(username)
        await self._page.locator('input[type="password"]').fill(password)
        return AuthResult(
            success=False,
            status="captcha_required",
            message=(
                "Chrome not found for CDP login. Install Google Chrome or pass "
                "options.storage_state after a manual Turnstile login."
            ),
        )

    async def get_accounts(self) -> list[NormalizedAccount]:
        self._require_auth()
        text = await self._load_text(api_paths.MODERN_DASHBOARD, cache_key="dashboard")
        accounts = [parser.account_from_portal_text(text)]
        self._accounts_cache = accounts
        return accounts

    async def get_holdings(self, account: NormalizedAccount) -> list[NormalizedHolding]:
        self._require_auth()
        text = await self._load_text(api_paths.MODERN_PORTFOLIO, cache_key="portfolio")
        return parser.holdings_from_portfolio_text(
            text, default_currency=account.currency or "NGN"
        )

    async def get_cash_balance(self, account: NormalizedAccount):
        self._require_auth()
        text = await self._load_text(api_paths.MODERN_WALLET, cache_key="wallet")
        return parser.cash_from_wallet_text(text)

    async def get_portfolio_summary(self, account: NormalizedAccount) -> PortfolioSummary | None:
        self._require_auth()
        from decimal import Decimal, InvalidOperation

        text = await self._load_text(api_paths.MODERN_DASHBOARD, cache_key="dashboard")
        total_val = None
        m = re.search(r"TOTAL PORTFOLIO VALUE\s*\n\s*(₦[\d,]+(?:\.\d+)?)", text, re.I)
        if m:
            try:
                total_val = Decimal(re.sub(r"[₦,\s]", "", m.group(1)))
            except (InvalidOperation, ValueError):
                total_val = None
        cash = parser.cash_from_wallet_text(
            await self._load_text(api_paths.MODERN_WALLET, cache_key="wallet")
        )
        if total_val is None and cash is None:
            return None
        return PortfolioSummary(
            total_market_value=total_val,
            cash_value=cash,
            currency="NGN",
            valuation_source="broker_supplied",
        )

    async def get_deposit_instructions(self) -> DepositInstructions:
        self._require_auth()
        assert self._page is not None
        # Always refresh wallet — VA may appear only after KYC / Fund click
        self._page_cache.pop("wallet", None)
        await self._safe_goto(api_paths.MODERN_WALLET)
        await self._page.wait_for_timeout(3500)
        if "/sign-in" in self._page.url:
            raise AppError(
                ErrorCode.INVALID_CREDENTIALS,
                "Morgan session expired — re-authenticate",
                status_code=401,
            )
        for label in (
            "Fund",
            "＋ Fund",
            "Virtual account",
            "YOUR VIRTUAL ACCOUNT",
            "Copy",
            "Account details",
            "Show account",
        ):
            try:
                loc = self._page.get_by_text(label, exact=False).first
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=2000)
                    await self._page.wait_for_timeout(2000)
            except Exception:
                continue
        # Also try buttons
        for name in ("Fund", "Copy", "Show"):
            try:
                btn = self._page.get_by_role("button", name=re.compile(name, re.I)).first
                if await btn.count():
                    await btn.click(timeout=1500)
                    await self._page.wait_for_timeout(1500)
            except Exception:
                continue
        text = await self._page.inner_text("body")
        self._page_cache["wallet"] = text
        try:
            return parser.deposit_instructions_from_wallet_text(text)
        except ValueError as exc:
            raise AppError(
                ErrorCode.CONNECTOR_NOT_IMPLEMENTED
                if "KYC" in str(exc)
                else ErrorCode.PORTFOLIO_EXTRACTION_FAILED,
                str(exc),
                status_code=501 if "KYC" in str(exc) else 502,
            ) from exc

    async def list_markets(self) -> list[MarketInfo]:
        self._require_auth()
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
        mid = (market_id or "NGX").upper()
        text = await self._load_text(api_paths.MODERN_MARKETS, cache_key="markets")
        return parser.instruments_from_markets_text(
            text, market_id=mid, page=page, per_page=per_page, search=search
        )

    async def place_order(self, order: OrderRequest) -> OrderResult:
        self._require_auth()
        assert self._page is not None
        symbol = (order.symbol or "").upper().strip()
        if not symbol:
            raise AppError(ErrorCode.VALIDATION_ERROR, "symbol is required", status_code=422)
        await self._safe_goto(api_paths.market_instrument(symbol))
        await self._page.wait_for_timeout(2500)
        text = await self._page.inner_text("body")
        if re.search(r"Verify your identity|Complete your KYC|unlock trading", text, re.I):
            raise AppError(
                ErrorCode.CONNECTOR_NOT_IMPLEMENTED,
                "Morgan equity trading is locked until KYC is completed "
                "(observed dashboard/wallet banners).",
                status_code=501,
            )
        side_label = "Buy" if order.side.value == "buy" else "Sell"
        btn = self._page.get_by_role("button", name=re.compile(side_label, re.I)).first
        try:
            await btn.click(timeout=5000)
        except Exception as exc:
            raise AppError(
                ErrorCode.CONNECTOR_NOT_IMPLEMENTED,
                f"Morgan {side_label} control not available on /markets/{symbol}",
                status_code=501,
            ) from exc
        raise AppError(
            ErrorCode.CONNECTOR_NOT_IMPLEMENTED,
            "Morgan order ticket UI was opened but the submit API is not yet "
            "captured for this account (KYC / empty wallet).",
            status_code=501,
        )

    async def test_connection(self) -> AuthResult:
        try:
            await self._ensure_browser()
            assert self._page is not None
            await self._safe_goto(api_paths.MODERN_SIGN_IN)
            email_el = await self._first_locator(selectors.MODERN_EMAIL_SELECTORS)
            if not email_el:
                return AuthResult(
                    success=False,
                    status="failed",
                    message="Morgan sign-in page reachable but form not found",
                )
            return AuthResult(
                success=True,
                status="authenticated",
                message="Morgan sign-in page reachable and form detected",
            )
        except Exception:
            logger.exception("Morgan test_connection failed")
            return AuthResult(
                success=False,
                status="failed",
                message="Unable to reach Morgan Capital sign-in page",
            )
        finally:
            await self.disconnect()

    async def disconnect(self) -> None:
        self._authenticated = False
        self._accounts_cache = None
        self._page_cache = {}
        try:
            if self._context and self._chrome_proc is None:
                await self._context.close()
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    pass
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            logger.exception("Error during Morgan disconnect (ignored)")
        finally:
            if self._chrome_proc:
                try:
                    self._chrome_proc.terminate()
                except Exception:
                    pass
                self._chrome_proc = None
            if self._chrome_profile:
                shutil.rmtree(self._chrome_profile, ignore_errors=True)
                self._chrome_profile = None
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None

    def _require_auth(self) -> None:
        if not self._authenticated or not self._page:
            raise AppError(
                ErrorCode.INVALID_CREDENTIALS,
                "Not authenticated with Morgan Capital",
                status_code=401,
            )

    async def _load_text(self, url: str, *, cache_key: str) -> str:
        if cache_key in self._page_cache:
            return self._page_cache[cache_key]
        assert self._page is not None
        await self._safe_goto(url)
        await self._page.wait_for_timeout(3500)
        if "/sign-in" in self._page.url:
            raise AppError(
                ErrorCode.INVALID_CREDENTIALS,
                "Morgan session expired — re-authenticate",
                status_code=401,
            )
        text = await self._page.inner_text("body")
        self._page_cache[cache_key] = text
        return text

    async def _ensure_browser(
        self,
        headless: bool | None = None,
        storage_state: dict[str, Any] | str | None = None,
    ) -> None:
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
        launch_kwargs: dict[str, Any] = {
            "headless": settings.playwright_headless if headless is None else headless,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        try:
            self._browser = await self._playwright.chromium.launch(
                channel="chrome", **launch_kwargs
            )
        except Exception:
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        ctx_kwargs: dict[str, Any] = {"accept_downloads": False, "java_script_enabled": True}
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state
        self._context = await self._browser.new_context(**ctx_kwargs)
        self._page = await self._context.new_page()
        self._page.set_default_timeout(settings.playwright_timeout_ms)

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

    async def _first_locator(self, selector_list: list[str]):
        assert self._page is not None
        for sel in selector_list:
            loc = self._page.locator(sel).first
            try:
                if await self._page.locator(sel).count() > 0 and await loc.is_visible():
                    return loc
            except Exception:
                continue
        return None

    async def _any_visible(self, selector_list: list[str]) -> bool:
        return (await self._first_locator(selector_list)) is not None
