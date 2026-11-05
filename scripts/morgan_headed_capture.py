#!/usr/bin/env python3
"""Headed Morgan Capital modern-app login → storage_state + API capture.

Uses a persistent Chrome profile under logs/morgan_chrome_profile so a
completed Turnstile session can be reused. Auto-clicks the Turnstile
checkbox via mouse coords on the iframe (fast), then submits.

Usage:
  $env:MORGAN_USER='...'
  $env:MORGAN_PASS='...'
  python scripts/morgan_headed_capture.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SIGN_IN = "https://www.morgancapitalgroup.com/sign-in"
PAGES = [
    "https://www.morgancapitalgroup.com/dashboard",
    "https://www.morgancapitalgroup.com/portfolio",
    "https://www.morgancapitalgroup.com/wallet",
    "https://www.morgancapitalgroup.com/markets",
    "https://www.morgancapitalgroup.com/markets/equities",
    "https://www.morgancapitalgroup.com/trade",
    "https://www.morgancapitalgroup.com/orders",
]

LOGS = ROOT / "logs"
PROFILE = LOGS / "morgan_chrome_profile"
STATE_PATH = LOGS / "morgan_storage_state.json"
OUT_PATH = LOGS / "morgan_headed_capture.json"
STATUS_PATH = LOGS / "morgan_headed_status.json"


def _interesting(url: str, method: str, ct: str) -> bool:
    u = url.lower()
    if "morgancapital" not in u and "supabase" not in u:
        return False
    if any(x in u for x in ("/_next/static", "/favicon", ".woff", ".png", ".svg", ".css")):
        return False
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        return True
    if "/api" in u or "supabase" in u or "json" in (ct or "").lower():
        return True
    if u.endswith(".json"):
        return True
    return False


def redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(
                x in lk
                for x in (
                    "password",
                    "token",
                    "authorization",
                    "secret",
                    "cookie",
                    "access_token",
                    "refresh",
                    "session",
                )
            ):
                out[k] = "***"
            elif any(x in lk for x in ("account_number", "nuban", "accountnumber")):
                s = str(v)
                out[k] = (s[:2] + "***" + s[-2:]) if len(s) > 4 else "***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


async def write_status(payload: dict) -> None:
    STATUS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


async def main() -> int:
    user = os.environ.get("MORGAN_USER", "").strip()
    password = os.environ.get("MORGAN_PASS", "")
    if not user or not password:
        print("MORGAN_USER and MORGAN_PASS required", file=sys.stderr)
        return 2

    wait_sec = int(os.environ.get("CAPTCHA_WAIT_SEC", "120"))
    LOGS.mkdir(exist_ok=True)
    PROFILE.mkdir(exist_ok=True)

    from playwright.async_api import async_playwright

    out: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "authenticated": False,
        "final_url": None,
        "storage_state_path": str(STATE_PATH),
        "responses": [],
        "requests": [],
        "pages_visited": [],
        "notes": [],
    }
    seen_req: set[str] = set()
    seen_resp: set[str] = set()

    async with async_playwright() as p:
        launch_args = ["--disable-blink-features=AutomationControlled"]
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE),
                channel="chrome",
                headless=False,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                args=launch_args,
            )
            out["notes"].append("persistent chrome profile")
        except Exception as exc:
            out["notes"].append(f"chrome persistent failed: {exc}; chromium")
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE),
                headless=False,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                args=launch_args,
            )

        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(45000)

        async def on_req(req):
            key = f"{req.method} {req.url}"
            if key in seen_req:
                return
            ct = req.headers.get("content-type", "")
            if not _interesting(req.url, req.method, ct):
                return
            seen_req.add(key)
            out["requests"].append(
                {
                    "method": req.method,
                    "url": req.url,
                    "post": (req.post_data or "")[:2000],
                }
            )

        async def on_resp(resp):
            key = f"{resp.status} {resp.request.method} {resp.url}"
            if key in seen_resp:
                return
            ct = resp.headers.get("content-type", "")
            if not _interesting(resp.url, resp.request.method, ct):
                return
            seen_resp.add(key)
            body_text = ""
            parsed = None
            try:
                body_text = await resp.text()
            except Exception:
                body_text = ""
            if body_text and body_text[:1] in "{[":
                try:
                    parsed = json.loads(body_text)
                except Exception:
                    parsed = None
            out["responses"].append(
                {
                    "status": resp.status,
                    "method": resp.request.method,
                    "url": resp.url,
                    "content_type": ct,
                    "body_preview": body_text[:4000],
                    "json": redact(parsed) if parsed is not None else None,
                }
            )

        page.on("request", on_req)
        page.on("response", on_resp)

        await write_status({"phase": "loading_sign_in", "url": SIGN_IN})
        await page.goto(SIGN_IN, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        # Already logged in via persistent profile?
        if "/sign-in" not in page.url:
            out["notes"].append(f"already authenticated at {page.url}")
            out["authenticated"] = True
        else:
            email = page.locator('input[type="email"]').first
            pw = page.locator('input[type="password"]').first
            await email.wait_for(state="visible", timeout=30000)
            await email.fill(user)
            await pw.fill(password)

            async def turnstile_state():
                return await page.evaluate(
                    """() => {
                        const btn = document.querySelector('button[type="submit"]');
                        const tok = document.querySelector('input[name="cf-turnstile-response"]');
                        const tokOk = !!(tok && tok.value && tok.value.length > 20);
                        const btnOk = !!(btn && !btn.disabled);
                        return {btnOk, tokOk, disabled: btn ? btn.disabled : null,
                                tokLen: tok ? (tok.value||'').length : 0};
                    }"""
                )

            async def click_turnstile_fast() -> str:
                loc = page.locator('iframe[src*="challenges.cloudflare.com"]').first
                try:
                    await loc.wait_for(state="visible", timeout=8000)
                except Exception:
                    return "iframe not visible"
                box = await loc.bounding_box()
                if not box:
                    return "no bbox"
                # Checkbox sits near the left of the widget
                await page.mouse.click(box["x"] + 26, box["y"] + box["height"] / 2)
                return f"clicked at ({box['x']+26:.0f},{box['y']+box['height']/2:.0f})"

            note = await click_turnstile_fast()
            out["notes"].append(f"turnstile: {note}")
            await write_status(
                {
                    "phase": "waiting_turnstile",
                    "message": f"Clicked Turnstile ({note}). Waiting up to {wait_sec}s…",
                }
            )

            deadline = time.monotonic() + wait_sec
            ready = False
            last_click = 0.0
            while time.monotonic() < deadline:
                st = await turnstile_state()
                if st.get("btnOk") or st.get("tokOk"):
                    ready = True
                    break
                if time.monotonic() - last_click > 8:
                    out["notes"].append(await click_turnstile_fast())
                    last_click = time.monotonic()
                await write_status(
                    {
                        "phase": "waiting_turnstile",
                        "turnstile": st,
                        "remaining_s": int(deadline - time.monotonic()),
                        "message": "Waiting for Turnstile token (click checkbox in Chrome if still shown)",
                    }
                )
                await page.wait_for_timeout(1500)

            if not ready:
                out["notes"].append("Turnstile wait timed out")
                out["final_url"] = page.url
                OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
                await write_status({"phase": "failed", "message": "Turnstile did not complete"})
                await context.close()
                return 1

            await write_status({"phase": "submitting", "message": "Clicking Sign in"})
            submit = page.locator('button[type="submit"]').first
            try:
                async with page.expect_response(
                    lambda r: r.request.method == "POST" and "sign-in" in r.url,
                    timeout=45000,
                ) as resp_info:
                    await submit.click()
                resp = await resp_info.value
                body = await resp.text()
                out["notes"].append(f"sign-in POST {resp.status}: {body[:500]}")
                if "captcha" in body.lower() and "disallowed" in body.lower():
                    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
                    await write_status({"phase": "failed", "message": "captcha rejected"})
                    await context.close()
                    return 1
            except Exception as exc:
                out["notes"].append(f"submit: {exc}")
                await submit.click(force=True)
                await page.wait_for_timeout(4000)

            for _ in range(40):
                if "/sign-in" not in page.url:
                    break
                await page.wait_for_timeout(500)

            if "/sign-in" in page.url:
                out["final_url"] = page.url
                out["notes"].append("still on sign-in")
                OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
                await write_status({"phase": "failed", "message": "Login did not leave /sign-in"})
                await context.close()
                return 1
            out["authenticated"] = True

        out["final_url"] = page.url
        await context.storage_state(path=str(STATE_PATH))
        await write_status(
            {
                "phase": "authenticated",
                "url": page.url,
                "message": "Logged in — capturing APIs",
            }
        )

        for url in PAGES:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(3500)
                out["pages_visited"].append({"url": page.url, "ok": True})
            except Exception as exc:
                out["pages_visited"].append({"url": url, "ok": False, "error": str(exc)})

        for label in ("Fund", "Deposit", "Wallet", "Buy", "Sell", "Equities"):
            try:
                loc = page.get_by_text(label, exact=False).first
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=2000)
                    await page.wait_for_timeout(2000)
            except Exception:
                pass

        await context.storage_state(path=str(STATE_PATH))
        out["finished_at"] = datetime.now(timezone.utc).isoformat()
        OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
        await write_status(
            {
                "phase": "done",
                "authenticated": True,
                "responses": len(out["responses"]),
                "requests": len(out["requests"]),
                "message": f"Capture complete → {OUT_PATH.name}",
            }
        )
        await context.close()
        print(json.dumps({"ok": True, "state": str(STATE_PATH), "out": str(OUT_PATH)}))
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
