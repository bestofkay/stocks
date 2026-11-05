#!/usr/bin/env python3
"""Fresh Chrome profile headed login for Morgan modern app.

Prints console/page errors. Waits for Turnstile iframe or manual solve.
Saves storage_state + browses for API capture on success.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOGS = ROOT / "logs"
PROFILE = LOGS / "morgan_chrome_profile_fresh"
STATE = LOGS / "morgan_storage_state.json"
OUT = LOGS / "morgan_headed_capture.json"
STATUS = LOGS / "morgan_headed_status.json"

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


def redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(x in lk for x in ("password", "token", "authorization", "secret", "cookie", "session")):
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


async def write_status(d: dict) -> None:
    STATUS.write_text(json.dumps(d, indent=2), encoding="utf-8")


async def main() -> int:
    user = os.environ["MORGAN_USER"].strip()
    password = os.environ["MORGAN_PASS"]
    wait_sec = int(os.environ.get("CAPTCHA_WAIT_SEC", "300"))

    if PROFILE.exists():
        shutil.rmtree(PROFILE, ignore_errors=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(exist_ok=True)

    from playwright.async_api import async_playwright

    out: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "console": [],
        "page_errors": [],
        "responses": [],
        "requests": [],
        "notes": [],
        "authenticated": False,
    }
    seen: set[str] = set()

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            channel="chrome",
            headless=False,
            viewport={"width": 1400, "height": 900},
            locale="en-US",
            timezone_id="Africa/Lagos",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            ignore_default_args=["--enable-automation"],
        )
        await ctx.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = window.chrome || { runtime: {} };
            """
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        page.set_default_timeout(60000)

        page.on("console", lambda m: out["console"].append({"type": m.type, "text": m.text[:500]}))
        page.on("pageerror", lambda e: out["page_errors"].append(str(e)[:500]))

        async def on_resp(resp):
            u = resp.url
            if "morgancapital" not in u and "supabase" not in u:
                return
            if any(x in u for x in ("/_next/static", ".css", ".woff", ".png", ".svg")):
                return
            ct = resp.headers.get("content-type", "")
            interesting = (
                resp.request.method in {"POST", "PUT", "PATCH"}
                or "/api" in u
                or "json" in ct
                or "supabase" in u
            )
            if not interesting:
                return
            key = f"{resp.status} {resp.request.method} {u}"
            if key in seen:
                return
            seen.add(key)
            text = ""
            parsed = None
            try:
                text = await resp.text()
                if text[:1] in "{[":
                    parsed = json.loads(text)
            except Exception:
                pass
            out["responses"].append(
                {
                    "status": resp.status,
                    "method": resp.request.method,
                    "url": u,
                    "body_preview": text[:4000],
                    "json": redact(parsed) if parsed is not None else None,
                }
            )

        page.on("response", on_resp)

        await write_status({"phase": "loading", "message": "Opening sign-in (fresh profile)"})
        await page.goto(SIGN_IN, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        await page.locator('input[type="email"]').fill(user)
        await page.locator('input[type="password"]').fill(password)

        # Patch turnstile.render to log failures
        await page.evaluate(
            """() => {
              if (typeof turnstile === 'undefined') return;
              const orig = turnstile.render.bind(turnstile);
              turnstile.render = function(el, opts) {
                const o = Object.assign({}, opts || {});
                const prevCb = o.callback;
                const prevErr = o['error-callback'];
                o.callback = (t) => { console.log('TS_OK', (t||'').length); if (prevCb) prevCb(t); };
                o['error-callback'] = (e) => { console.error('TS_ERR', e); if (prevErr) prevErr(e); };
                try { return orig(el, o); } catch (e) { console.error('TS_RENDER_THROW', e); throw e; }
              };
            }"""
        )
        # Force re-mount by soft reload of the form widget if empty after wait
        await page.wait_for_timeout(5000)
        state = await page.evaluate(
            """() => ({
              iframes: document.querySelectorAll('iframe').length,
              host: document.querySelector('.mt-4.flex.justify-center')?.innerHTML?.slice(0,300),
              hasTurnstile: typeof turnstile !== 'undefined'
            })"""
        )
        out["notes"].append(f"initial_state={state}")

        if state.get("iframes", 0) == 0 and state.get("hasTurnstile"):
            # Force explicit render into host
            forced = await page.evaluate(
                """() => {
                  const host = document.querySelector('.mt-4.flex.justify-center > div');
                  if (!host) return 'no-host';
                  const mount = document.createElement('div');
                  host.prepend(mount);
                  try {
                    const id = turnstile.render(mount, {
                      sitekey: '0x4AAAAAAEkSMulb_IeE7kco',
                      callback: (token) => {
                        const inp = document.querySelector('input[name=cf-turnstile-response]');
                        if (inp) { inp.value = token; inp.dispatchEvent(new Event('input', {bubbles:true})); }
                        const btn = document.querySelector('button[type=submit]');
                        if (btn) btn.disabled = false;
                        console.log('TS_FORCED_OK', token.length);
                      },
                      'error-callback': (e) => console.error('TS_FORCED_ERR', e)
                    });
                    return 'rendered:' + id;
                  } catch (e) { return 'throw:' + e; }
                }"""
            )
            out["notes"].append(f"forced_render={forced}")

        await write_status(
            {
                "phase": "waiting_turnstile",
                "message": (
                    f"Complete the Cloudflare checkbox in the Chrome window if shown "
                    f"(waiting {wait_sec}s). Do not close the window."
                ),
            }
        )

        deadline = time.monotonic() + wait_sec
        ready = False
        while time.monotonic() < deadline:
            st = await page.evaluate(
                """() => {
                  const tok = document.querySelector('input[name=cf-turnstile-response]');
                  const btn = document.querySelector('button[type=submit]');
                  return {
                    tokLen: tok ? (tok.value||'').length : 0,
                    btnDisabled: btn ? btn.disabled : null,
                    iframes: [...document.querySelectorAll('iframe')].map(i => i.src.slice(0,80)),
                    url: location.href
                  };
                }"""
            )
            # Also try clicking checkbox if iframe appeared
            if st["iframes"]:
                try:
                    box = await page.locator('iframe[src*="challenges.cloudflare.com"]').first.bounding_box()
                    if box:
                        await page.mouse.click(box["x"] + 26, box["y"] + box["height"] / 2)
                except Exception:
                    pass
            if st["tokLen"] > 20 or st["btnDisabled"] is False:
                ready = True
                break
            await write_status(
                {
                    "phase": "waiting_turnstile",
                    "turnstile": st,
                    "remaining_s": int(deadline - time.monotonic()),
                    "message": "Waiting for Turnstile — click Verify you are human in Chrome if visible",
                }
            )
            await page.wait_for_timeout(2000)

        if not ready:
            out["notes"].append("timeout waiting for turnstile")
            out["console"] = out["console"][-40:]
            OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
            await write_status({"phase": "failed", "message": "Turnstile never completed", "notes": out["notes"]})
            await ctx.close()
            return 1

        await write_status({"phase": "submitting"})
        try:
            async with page.expect_response(
                lambda r: r.request.method == "POST" and "sign-in" in r.url, timeout=45000
            ) as ri:
                await page.locator('button[type="submit"]').click()
            resp = await ri.value
            body = await resp.text()
            out["notes"].append(f"sign-in {resp.status}: {body[:400]}")
            if "captcha" in body.lower() and "disallowed" in body.lower():
                OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
                await write_status({"phase": "failed", "message": "captcha rejected"})
                await ctx.close()
                return 1
        except Exception as exc:
            out["notes"].append(f"submit err {exc}")
            await page.locator('button[type="submit"]').click(force=True)
            await page.wait_for_timeout(4000)

        for _ in range(40):
            if "/sign-in" not in page.url:
                break
            await page.wait_for_timeout(500)

        if "/sign-in" in page.url:
            out["final_url"] = page.url
            OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
            await write_status({"phase": "failed", "message": "still on sign-in"})
            await ctx.close()
            return 1

        out["authenticated"] = True
        out["final_url"] = page.url
        await ctx.storage_state(path=str(STATE))
        await write_status({"phase": "authenticated", "url": page.url, "message": "Capturing APIs"})

        for url in PAGES:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(3500)
            except Exception as exc:
                out["notes"].append(f"nav {url}: {exc}")

        await ctx.storage_state(path=str(STATE))
        out["finished_at"] = datetime.now(timezone.utc).isoformat()
        out["console"] = out["console"][-50:]
        OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
        await write_status(
            {
                "phase": "done",
                "authenticated": True,
                "responses": len(out["responses"]),
                "state": str(STATE),
            }
        )
        print(json.dumps({"ok": True, "state": str(STATE), "responses": len(out["responses"])}))
        await ctx.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
