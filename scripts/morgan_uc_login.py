#!/usr/bin/env python3
"""Morgan modern-app login via SeleniumBase UC Mode (better Turnstile success)."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOGS = ROOT / "logs"
STATE = LOGS / "morgan_storage_state.json"
OUT = LOGS / "morgan_headed_capture.json"
STATUS = LOGS / "morgan_headed_status.json"
SIGN_IN = "https://www.morgancapitalgroup.com/sign-in"
ACTION = "7007449683358a67772b2bea04983b29fdd55abe1f"
SITEKEY = "0x4AAAAAAEkSMulb_IeE7kco"

PAGES = [
    "https://www.morgancapitalgroup.com/dashboard",
    "https://www.morgancapitalgroup.com/portfolio",
    "https://www.morgancapitalgroup.com/wallet",
    "https://www.morgancapitalgroup.com/markets",
    "https://www.morgancapitalgroup.com/markets/equities",
]


def status(d: dict) -> None:
    STATUS.write_text(json.dumps(d, indent=2), encoding="utf-8")


def main() -> int:
    user = os.environ["MORGAN_USER"].strip()
    password = os.environ["MORGAN_PASS"]
    wait_sec = int(os.environ.get("CAPTCHA_WAIT_SEC", "300"))
    LOGS.mkdir(exist_ok=True)

    from seleniumbase import Driver

    out: dict = {"mode": "seleniumbase_uc", "notes": [], "responses": [], "authenticated": False}
    status({"phase": "launching", "message": "Starting UC Chrome"})

    driver = Driver(uc=True, headless=False)
    try:
        driver.uc_open_with_reconnect(SIGN_IN, reconnect_time=4)
        time.sleep(3)
        driver.type('input[type="email"]', user)
        driver.type('input[type="password"]', password)
        time.sleep(2)

        # Try UC click on turnstile if present
        try:
            driver.uc_gui_click_captcha()
            out["notes"].append("uc_gui_click_captcha attempted")
        except Exception as exc:
            out["notes"].append(f"uc_gui_click_captcha: {exc}")

        # Also force-render always-visible widget
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
            SITEKEY,
        )

        status({"phase": "waiting_turnstile", "message": "Waiting for token / click Verify if shown"})
        deadline = time.time() + wait_sec
        token = ""
        while time.time() < deadline:
            try:
                driver.uc_gui_click_captcha()
            except Exception:
                pass
            token = driver.execute_script(
                """
                return window.__morganTsToken ||
                  (typeof turnstile!=='undefined' && turnstile.getResponse && turnstile.getResponse()) ||
                  (document.querySelector('input[name=cf-turnstile-response]')||{}).value || '';
                """
            ) or ""
            if len(token) > 20:
                break
            status(
                {
                    "phase": "waiting_turnstile",
                    "tokenLen": len(token),
                    "remaining_s": int(deadline - time.time()),
                    "message": "Click Verify you are human in Chrome if visible",
                }
            )
            time.sleep(2)

        if len(token) < 20:
            out["notes"].append("no token")
            OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
            status({"phase": "failed", "message": "No Turnstile token"})
            return 1

        status({"phase": "submitting"})
        result = driver.execute_async_script(
            """
            const email = arguments[0], password = arguments[1], token = arguments[2], actionId = arguments[3];
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
            }).then(r => r.text().then(t => done({status: r.status, text: t.slice(0,2000)})))
              .catch(e => done({error: String(e)}));
            """,
            user,
            password,
            token,
            ACTION,
        )
        out["notes"].append(f"action={result}")
        text = (result or {}).get("text") or ""
        if '"ok":false' in text.replace(" ", "") or "captcha" in text.lower() and "disallowed" in text.lower():
            OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
            status({"phase": "failed", "message": "signIn failed", "detail": text[:400]})
            return 1

        driver.get("https://www.morgancapitalgroup.com/dashboard")
        time.sleep(3)
        if "sign-in" in driver.current_url:
            OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
            status({"phase": "failed", "message": "still on sign-in", "detail": text[:400]})
            return 1

        out["authenticated"] = True
        # Save cookies as playwright storage_state-like json
        cookies = driver.get_cookies()
        storage = {
            "cookies": [
                {
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c.get("domain", ""),
                    "path": c.get("path", "/"),
                    "expires": c.get("expiry", -1),
                    "httpOnly": c.get("httpOnly", False),
                    "secure": c.get("secure", False),
                    "sameSite": c.get("sameSite", "Lax"),
                }
                for c in cookies
            ],
            "origins": [],
        }
        STATE.write_text(json.dumps(storage, indent=2), encoding="utf-8")

        # Capture XHR while browsing
        for url in PAGES:
            try:
                driver.get(url)
                time.sleep(3)
            except Exception as exc:
                out["notes"].append(f"nav {url}: {exc}")

        # Refresh cookies after browse
        cookies = driver.get_cookies()
        storage["cookies"] = [
            {
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain", ""),
                "path": c.get("path", "/"),
                "expires": c.get("expiry", -1),
                "httpOnly": c.get("httpOnly", False),
                "secure": c.get("secure", False),
                "sameSite": c.get("sameSite", "Lax"),
            }
            for c in cookies
        ]
        STATE.write_text(json.dumps(storage, indent=2), encoding="utf-8")
        out["final_url"] = driver.current_url
        OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
        status({"phase": "done", "authenticated": True, "state": str(STATE)})
        print(json.dumps({"ok": True, "state": str(STATE)}))
        return 0
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
