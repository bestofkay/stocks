#!/usr/bin/env python3
"""Single-shot: CDP Chrome login → storage_state → scrape portfolio/wallet/markets/orders."""
from __future__ import annotations

import asyncio
import json
import os
import re
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
OUT = LOGS / "morgan_session_capture.json"
STATUS = LOGS / "morgan_headed_status.json"
PORT = int(os.environ.get("MORGAN_CDP_PORT", "9334"))
ACTION = "7007449683358a67772b2bea04983b29fdd55abe1f"
SITEKEY = "0x4AAAAAAEkSMulb_IeE7kco"
SIGN_IN = "https://www.morgancapitalgroup.com/sign-in"


def find_chrome() -> str:
    for c in (
        os.environ.get("CHROME_PATH", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ):
        if c and Path(c).exists():
            return c
    raise FileNotFoundError("Chrome not found")


async def write_status(d: dict) -> None:
    STATUS.write_text(json.dumps(d, indent=2), encoding="utf-8")


async def scrape_page(page, name: str) -> dict:
    await page.wait_for_timeout(4000)
    text = await page.inner_text("body")
    data = {
        "url": page.url,
        "text": text[:6000],
        "money": re.findall(r"₦[\d,]+(?:\.\d+)?", text),
        "nums10": re.findall(r"\b\d{10}\b", text),
        "banks": re.findall(
            r"Wema(?:\s+Bank)?|GTBank|Access Bank|Zenith Bank|Moniepoint|Providus",
            text,
            re.I,
        ),
    }
    if name == "markets":
        hrefs = await page.eval_on_selector_all(
            "a[href*='/markets/']",
            "els => [...new Set(els.map(e => e.getAttribute('href')).filter(Boolean))]",
        )
        data["market_hrefs"] = hrefs
        rows = []
        for a in await page.locator("a[href*='/markets/']").all():
            try:
                t = (await a.inner_text()).strip()
                h = await a.get_attribute("href")
                if t and h:
                    rows.append({"href": h, "text": t[:200]})
            except Exception:
                pass
        data["market_rows"] = rows[:120]
    if name == "wallet":
        for label in ("Fund", "Virtual", "Account details", "Copy"):
            try:
                loc = page.get_by_text(label, exact=False).first
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=1500)
                    await page.wait_for_timeout(2000)
                    t2 = await page.inner_text("body")
                    data[f"after_{label}"] = t2[:3000]
                    data["nums10"] += re.findall(r"\b\d{10}\b", t2)
                    data["banks"] += re.findall(
                        r"Wema(?:\s+Bank)?|GTBank|Access Bank|Zenith Bank|Moniepoint|Providus",
                        t2,
                        re.I,
                    )
            except Exception:
                pass
    if name == "zenith":
        data["buttons"] = []
        for label in ("Buy", "Sell", "Place order", "Review"):
            try:
                if await page.get_by_role("button", name=re.compile(label, re.I)).count():
                    data["buttons"].append(label)
            except Exception:
                pass
    return data


async def main() -> int:
    user = os.environ["MORGAN_USER"].strip()
    password = os.environ["MORGAN_PASS"]
    wait_sec = int(os.environ.get("CAPTCHA_WAIT_SEC", "300"))
    LOGS.mkdir(exist_ok=True)

    profile = Path(tempfile.mkdtemp(prefix="morgan_cap_"))
    proc = subprocess.Popen(
        [
            find_chrome(),
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
    await write_status({"phase": "launching", "pid": proc.pid})
    await asyncio.sleep(3)

    from playwright.async_api import async_playwright

    out: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "authenticated": False,
        "pages": {},
        "notes": [],
    }

    async with async_playwright() as p:
        browser = None
        for i in range(25):
            try:
                browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
                break
            except Exception:
                await asyncio.sleep(1)
        if not browser:
            await write_status({"phase": "failed", "message": "cdp connect failed"})
            proc.terminate()
            return 1

        ctx = browser.contexts[0]
        page = ctx.pages[0]
        if "sign-in" not in page.url:
            await page.goto(SIGN_IN)
        await page.wait_for_timeout(2000)
        await page.locator('input[type="email"]').fill(user)
        await page.locator('input[type="password"]').fill(password)
        await page.wait_for_function("() => typeof turnstile !== 'undefined'", timeout=30000)

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
            SITEKEY,
        )
        await write_status({"phase": "waiting_turnstile"})

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
            await write_status(
                {
                    "phase": "waiting_turnstile",
                    "remaining_s": int(deadline - time.monotonic()),
                    "tokenLen": len(token or ""),
                }
            )
            await page.wait_for_timeout(2000)

        if not token or len(token) < 20:
            out["notes"].append("no token")
            OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
            await write_status({"phase": "failed", "message": "no turnstile token"})
            proc.terminate()
            return 1

        result = await page.evaluate(
            """async ({email, password, token, actionId}) => {
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
            {"email": user, "password": password, "token": token, "actionId": ACTION},
        )
        out["notes"].append(f"signIn={result}")
        await page.goto("https://www.morgancapitalgroup.com/dashboard", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        if "/sign-in" in page.url:
            OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
            await write_status({"phase": "failed", "message": "login did not stick", "detail": result})
            proc.terminate()
            return 1

        out["authenticated"] = True
        await ctx.storage_state(path=str(STATE))
        await write_status({"phase": "authenticated", "url": page.url})

        targets = [
            ("dashboard", "https://www.morgancapitalgroup.com/dashboard"),
            ("portfolio", "https://www.morgancapitalgroup.com/portfolio"),
            ("wallet", "https://www.morgancapitalgroup.com/wallet"),
            ("markets", "https://www.morgancapitalgroup.com/markets"),
            ("orders", "https://www.morgancapitalgroup.com/orders"),
            ("zenith", "https://www.morgancapitalgroup.com/markets/ZENITHBANK"),
        ]
        for name, url in targets:
            await page.goto(url, wait_until="domcontentloaded")
            out["pages"][name] = await scrape_page(page, name)
            await write_status({"phase": "capturing", "page": name})

        await ctx.storage_state(path=str(STATE))
        out["finished_at"] = datetime.now(timezone.utc).isoformat()
        OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
        await write_status({"phase": "done", "pages": list(out["pages"])})
        print(json.dumps({"ok": True, "pages": list(out["pages"]), "out": str(OUT)}))
        proc.terminate()
        shutil.rmtree(profile, ignore_errors=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
