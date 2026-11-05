"""Morgan Capital browser-session bootstrap (production-oriented).

Production practice
-------------------
1. Bootstrap rarely with SeleniumBase UC Mode (Turnstile needs a real display).
   On Linux servers use ``xvfb=True`` — never true Chrome ``headless`` for login.
2. Persist Playwright ``storage_state`` (cookies including ``mc_session``) in
   encrypted connection options via ``AuthResult.options_patch``.
3. Reuse that state for subsequent sync/deposit/market/order work in headless
   Playwright — no interactive browser on the hot path.
4. When the stored session fails (redirect to ``/sign-in``), re-run UC bootstrap
   and replace the encrypted state.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

from . import api_paths

logger = logging.getLogger(__name__)


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def uc_driver_kwargs(
    *,
    headless: bool | None = None,
    headed: bool | None = None,
) -> dict[str, Any]:
    """Launch options for SeleniumBase UC Turnstile login.

    True Chrome headless breaks Turnstile. Prefer:
    - Linux / servers: ``xvfb=True`` (virtual display)
    - Windows / local: headed Chrome
    """
    kwargs: dict[str, Any] = {"uc": True}
    if headless is True:
        kwargs["headless"] = True
        return kwargs
    if headed is True or (headed is None and not is_linux()):
        kwargs["headed"] = True
        kwargs["headless"] = False
        return kwargs
    # Linux production default
    kwargs["xvfb"] = True
    kwargs["headless"] = False
    return kwargs


def scrape_headless_default(options: dict[str, Any] | None = None) -> bool:
    """Headless Playwright for post-login scrape/reuse (not for Turnstile)."""
    options = options or {}
    if options.get("headless") is not None:
        return bool(options["headless"])
    if options.get("scrape_headless") is not None:
        return bool(options["scrape_headless"])
    return True


def cookies_to_storage_state(cookies: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert Selenium cookies to Playwright ``storage_state`` shape."""
    out: list[dict[str, Any]] = []
    for c in cookies:
        same = c.get("sameSite") or "Lax"
        if same not in {"Strict", "Lax", "None"}:
            same = "Lax"
        out.append(
            {
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain") or "www.morgancapitalgroup.com",
                "path": c.get("path") or "/",
                "expires": float(c["expiry"]) if c.get("expiry") is not None else -1,
                "httpOnly": bool(c.get("httpOnly", False)),
                "secure": bool(c.get("secure", False)),
                "sameSite": same,
            }
        )
    return {"cookies": out, "origins": []}


def storage_state_has_session(state: dict[str, Any] | str | None) -> bool:
    if isinstance(state, str):
        return bool(state.strip())
    if not isinstance(state, dict):
        return False
    cookies = state.get("cookies") or []
    return any(
        isinstance(c, dict) and c.get("name") == "mc_session" and c.get("value")
        for c in cookies
    ) or bool(cookies)


def uc_login(
    username: str,
    password: str,
    *,
    captcha_timeout_sec: int = 180,
    headless: bool | None = None,
    headed: bool | None = None,
) -> dict[str, Any]:
    """Blocking UC login. Returns ``{ok, storage_state?, status?, notes, detail?}``."""
    try:
        from seleniumbase import Driver
    except ImportError:
        return {
            "ok": False,
            "status": "unavailable",
            "notes": ["seleniumbase not installed"],
            "detail": "seleniumbase not installed",
        }

    notes: list[str] = []
    launch = uc_driver_kwargs(headless=headless, headed=headed)
    notes.append(f"driver={launch}")
    driver = Driver(**launch)
    try:
        driver.uc_open_with_reconnect(api_paths.MODERN_SIGN_IN, reconnect_time=4)
        time.sleep(2)
        driver.type('input[type="email"]', username)
        driver.type('input[type="password"]', password)
        time.sleep(1)
        try:
            driver.uc_gui_click_captcha()
            notes.append("uc_gui_click_captcha")
        except Exception as exc:
            notes.append(f"uc_gui_click_captcha: {exc}")

        driver.execute_script(
            """
            const host = document.querySelector('.mt-4.flex.justify-center');
            if (!host || typeof turnstile === 'undefined') return 'skip';
            host.innerHTML = '';
            const mount = document.createElement('div');
            host.appendChild(mount);
            const inp = document.createElement('input');
            inp.type = 'hidden'; inp.name = 'cf-turnstile-response';
            host.appendChild(inp);
            window.__morganTsToken = '';
            turnstile.render(mount, {
              sitekey: arguments[0],
              appearance: 'always', size: 'normal', theme: 'light',
              callback: (t) => { window.__morganTsToken = t; inp.value = t; }
            });
            return 'forced';
            """,
            api_paths.TURNSTILE_SITEKEY,
        )

        token = ""
        deadline = time.time() + captcha_timeout_sec
        while time.time() < deadline:
            try:
                driver.uc_gui_click_captcha()
            except Exception:
                pass
            token = (
                driver.execute_script(
                    """
                    return window.__morganTsToken ||
                      (typeof turnstile!=='undefined' && turnstile.getResponse && turnstile.getResponse()) ||
                      (document.querySelector('input[name=cf-turnstile-response]')||{}).value || '';
                    """
                )
                or ""
            )
            if len(token) > 20:
                break
            time.sleep(1.5)

        if len(token) < 20:
            return {"ok": False, "status": "captcha_required", "notes": notes}

        result = driver.execute_async_script(
            """
            const email = arguments[0], password = arguments[1],
                  token = arguments[2], actionId = arguments[3];
            const done = arguments[arguments.length - 1];
            fetch('/sign-in', {
              method: 'POST',
              headers: {
                'Next-Action': actionId,
                'Accept': 'text/x-component',
                'Content-Type': 'text/plain;charset=UTF-8',
              },
              body: JSON.stringify([email, password, token]),
              credentials: 'same-origin',
            }).then(r => r.text().then(t => done({status: r.status, text: t.slice(0,1500)})))
              .catch(e => done({error: String(e)}));
            """,
            username,
            password,
            token,
            api_paths.SIGN_IN_ACTION_ID,
        )
        text = (result or {}).get("text") or ""
        notes.append(f"signIn={str(result)[:200]}")
        if "captcha" in text.lower() and "disallowed" in text.lower():
            return {
                "ok": False,
                "status": "captcha_required",
                "notes": notes,
                "detail": text[:300],
            }
        if '"ok":false' in text.replace(" ", ""):
            return {
                "ok": False,
                "status": "failed",
                "notes": notes,
                "detail": text[:300],
            }

        driver.get(api_paths.MODERN_DASHBOARD)
        time.sleep(3)
        if "sign-in" in (driver.current_url or ""):
            return {
                "ok": False,
                "status": "failed",
                "notes": notes,
                "detail": "UC login did not stick on dashboard",
            }

        storage = cookies_to_storage_state(driver.get_cookies())
        return {
            "ok": True,
            "storage_state": storage,
            "notes": notes,
            "url": driver.current_url,
        }
    finally:
        try:
            driver.quit()
        except Exception:
            logger.exception("Morgan UC driver quit failed")
