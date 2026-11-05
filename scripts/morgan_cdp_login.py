#!/usr/bin/env python3
"""Login via real Chrome + CDP, force Turnstile, call Next.js signIn with token.

 bypasses React captcha state by invoking the server action directly once a
Turnstile token is available.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOGS = ROOT / "logs"
STATE = LOGS / "morgan_storage_state.json"
OUT = LOGS / "morgan_headed_capture.json"
STATUS = LOGS / "morgan_headed_status.json"
PORT = int(os.environ.get("MORGAN_CDP_PORT", "9333"))
# Observed Next.js server action id for signIn (from page chunk)
SIGN_IN_ACTION = "7007449683358a67772b2bea04983b29fdd55abe1f"

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


def find_chrome() -> str:
    candidates = [
        os.environ.get("CHROME_PATH", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        shutil.which("chrome") or "",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    raise FileNotFoundError("Google Chrome not found")


def redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(x in lk for x in ("password", "token", "authorization", "secret", "cookie", "session")):
                out[k] = "***"
            elif any(x in lk for x in ("account_number", "nuban")):
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
    LOGS.mkdir(exist_ok=True)

    profile = Path(tempfile.mkdtemp(prefix="morgan_cdp_"))
    chrome = find_chrome()
    proc = subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={PORT}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            SIGN_IN,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    await write_status({"phase": "launching_chrome", "port": PORT, "pid": proc.pid})
    await asyncio.sleep(3)

    from playwright.async_api import async_playwright

    out: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": "cdp_real_chrome",
        "responses": [],
        "notes": [],
        "authenticated": False,
    }
    seen: set[str] = set()

    async with async_playwright() as p:
        browser = None
        for attempt in range(25):
            try:
                browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
                break
            except Exception as exc:
                out["notes"].append(f"cdp connect try {attempt}: {exc}")
                await asyncio.sleep(1)
        if browser is None:
            await write_status({"phase": "failed", "message": "CDP connect failed"})
            proc.terminate()
            return 1

        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        async def on_resp(resp):
            u = resp.url
            if "morgancapital" not in u and "supabase" not in u:
                return
            if any(x in u for x in ("/_next/static", ".css", ".woff", ".png", ".svg", ".jpg")):
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

        if "sign-in" not in page.url:
            await page.goto(SIGN_IN, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        await page.locator('input[type="email"]').fill(user)
        await page.locator('input[type="password"]').fill(password)
        await page.wait_for_function("() => typeof turnstile !== 'undefined'", timeout=30000)

        forced = await page.evaluate(
            """() => {
              const host = document.querySelector('.mt-4.flex.justify-center');
              if (!host || typeof turnstile === 'undefined') return 'skip';
              host.innerHTML = '';
              const mount = document.createElement('div');
              host.appendChild(mount);
              const inp = document.createElement('input');
              inp.type = 'hidden';
              inp.name = 'cf-turnstile-response';
              host.appendChild(inp);
              window.__morganTsToken = '';
              try {
                const wid = turnstile.render(mount, {
                  sitekey: '0x4AAAAAAEkSMulb_IeE7kco',
                  theme: 'light',
                  size: 'normal',
                  appearance: 'always',
                  execution: 'render',
                  retry: 'auto',
                  callback: (token) => {
                    window.__morganTsToken = token || '';
                    inp.value = token || '';
                  },
                });
                return 'forced:' + wid;
              } catch (e) { return 'err:' + e; }
            }"""
        )
        out["notes"].append(f"force_turnstile={forced}")
        await write_status(
            {
                "phase": "waiting_turnstile",
                "message": "Complete Cloudflare Verify in Chrome if shown; waiting for token…",
            }
        )

        deadline = time.monotonic() + wait_sec
        token = ""
        while time.monotonic() < deadline:
            for fr in page.frames:
                if "challenge" not in fr.url and "turnstile" not in fr.url:
                    continue
                for sel in ('input[type="checkbox"]', '[role="checkbox"]', "label", ".cb-lb"):
                    try:
                        await fr.locator(sel).first.click(timeout=600, force=True)
                        out["notes"].append(f"clicked {sel}")
                        break
                    except Exception:
                        continue
            st = await page.evaluate(
                """() => {
                  let resp = '';
                  try { resp = (turnstile.getResponse && turnstile.getResponse()) || ''; } catch (e) {}
                  const inp = document.querySelector('input[name=cf-turnstile-response]');
                  const w = window.__morganTsToken || '';
                  const token = w || resp || (inp && inp.value) || '';
                  return {
                    tokenLen: token.length,
                    frames: [...document.querySelectorAll('iframe')].length,
                    url: location.href
                  };
                }"""
            )
            st["playwright_frames"] = [
                f.url[:140] for f in page.frames if f != page.main_frame
            ]
            if st["tokenLen"] > 20:
                token = await page.evaluate(
                    """() => window.__morganTsToken || (turnstile.getResponse&&turnstile.getResponse()) ||
                           (document.querySelector('input[name=cf-turnstile-response]')||{}).value || ''"""
                )
                break
            await write_status(
                {
                    "phase": "waiting_turnstile",
                    "turnstile": st,
                    "remaining_s": int(deadline - time.monotonic()),
                    "message": "Waiting for Turnstile token (click checkbox in Chrome if visible)",
                }
            )
            await page.wait_for_timeout(2000)

        if not token or len(token) < 20:
            out["notes"].append("no turnstile token")
            OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
            await write_status({"phase": "failed", "message": "No Turnstile token"})
            proc.terminate()
            return 1

        await write_status({"phase": "submitting", "message": "Calling signIn server action with token"})
        # Invoke Next.js server action directly so React captcha state is irrelevant
        action_result = await page.evaluate(
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
              const text = await res.text();
              return { status: res.status, text: text.slice(0, 2000) };
            }""",
            {
                "email": user,
                "password": password,
                "token": token,
                "actionId": SIGN_IN_ACTION,
            },
        )
        out["notes"].append(f"server_action={action_result}")

        # Parse ok from flight response
        text = (action_result or {}).get("text") or ""
        ok = '"ok":true' in text.replace(" ", "") or '"ok": true' in text
        if not ok and "captcha" in text.lower():
            OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
            await write_status({"phase": "failed", "message": "captcha rejected by server action", "detail": text[:300]})
            proc.terminate()
            return 1
        if not ok and '"ok":false' in text.replace(" ", ""):
            OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
            await write_status({"phase": "failed", "message": "signIn failed", "detail": text[:400]})
            proc.terminate()
            return 1

        await page.goto("https://www.morgancapitalgroup.com/dashboard", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        if "/sign-in" in page.url:
            # try refresh/dashboard again
            await page.goto("https://www.morgancapitalgroup.com/dashboard", wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
        if "/sign-in" in page.url:
            out["final_url"] = page.url
            OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
            await write_status({"phase": "failed", "message": "action returned but still redirected to sign-in", "detail": text[:400]})
            proc.terminate()
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
        proc.terminate()
        shutil.rmtree(profile, ignore_errors=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
