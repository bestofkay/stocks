#!/usr/bin/env python3
"""Morgan Capital auth: iTrade folio.Web.go + modern site login probes."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ITRADE = "https://itrade.morgancapitalgroup.com/customers/"


async def main() -> int:
    user = os.environ["MORGAN_USER"].strip()
    password = os.environ["MORGAN_PASS"]

    from playwright.async_api import async_playwright

    out: dict = {"responses": [], "network": [], "notes": []}
    seen = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(60000)

        async def on_resp(resp):
            u = resp.url
            if "morgan" not in u.lower():
                return
            interesting = any(
                x in u.lower()
                for x in ("folio", "auth", "api", "wa/", "login", "customer", "wallet", "portfolio")
            )
            if not interesting and resp.request.method == "GET":
                return
            try:
                text = await resp.text()
            except Exception:
                text = ""
            entry = {
                "status": resp.status,
                "method": resp.request.method,
                "url": u,
                "ct": resp.headers.get("content-type"),
                "body": text[:4000],
            }
            out["responses"].append(entry)
            print("RESP", resp.status, resp.request.method, u[:120], text[:100].replace("\n", " "))

        page.on("response", on_resp)

        # --- Classic iTrade ---
        await page.goto(ITRADE, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        try:
            await page.wait_for_function(
                "() => typeof folio !== 'undefined' && folio.Web && typeof folio.Web.go === 'function'",
                timeout=20000,
            )
            out["notes"].append("folio.Web.go ready")
        except Exception as exc:
            out["notes"].append(f"folio wait: {exc}")

        await page.fill("#un", user)
        await page.fill("#pw", password)
        # Enable button if still disabled, then invoke go()
        result = await page.evaluate(
            """([user, password]) => {
                const un = document.getElementById('un');
                const pw = document.getElementById('pw');
                const btn = document.getElementById('btn');
                if (un) un.value = user;
                if (pw) pw.value = password;
                if (btn) btn.disabled = false;
                if (window.folio && folio.Web && folio.Web.go) {
                  folio.Web.go(un, pw, btn,
                    '../folio/authCustomer?a=v',
                    '../folio/authCustomer',
                    '../folio/authCustomer?a=f',
                    '../folio/resetlogin?t=c');
                  return 'called folio.Web.go';
                }
                return 'folio.Web.go missing';
            }""",
            [user, password],
        )
        out["notes"].append(result)
        print("EVAL", result)
        await page.wait_for_timeout(10000)
        out["itrade_url"] = page.url
        out["itrade_body"] = (await page.inner_text("body"))[:2500]
        print("ITRADE URL", page.url)
        print("ITRADE BODY", out["itrade_body"][:600])

        # Try username local-part if email failed
        if await page.locator("#un").count():
            alt = user.split("@")[0]
            await page.evaluate(
                """([user, password]) => {
                    const un = document.getElementById('un');
                    const pw = document.getElementById('pw');
                    const btn = document.getElementById('btn');
                    if (un) un.value = user;
                    if (pw) pw.value = password;
                    if (btn) btn.disabled = false;
                    folio.Web.go(un, pw, btn,
                      '../folio/authCustomer?a=v',
                      '../folio/authCustomer',
                      '../folio/authCustomer?a=f',
                      '../folio/resetlogin?t=c');
                }""",
                [alt, password],
            )
            await page.wait_for_timeout(8000)
            out["itrade_retry_url"] = page.url
            out["itrade_retry_body"] = (await page.inner_text("body"))[:2000]
            print("RETRY", page.url, out["itrade_retry_body"][:400])

        # Probe folio endpoints with page.request after cookies (if any)
        for path in [
            "/folio/authCustomer",
            "/folio/authCustomer?a=v",
            "/customers/wa/",
            "/wa/",
            "/folio/",
        ]:
            url = "https://itrade.morgancapitalgroup.com" + path
            try:
                r = await page.request.get(url)
                t = await r.text()
                out["network"].append({"get": url, "status": r.status, "body": t[:500]})
                print("GET", path, r.status, t[:80].replace("\n", " "))
            except Exception as exc:
                out["network"].append({"get": url, "error": str(exc)})

        # --- Modern site: look for login/signup SPA routes ---
        modern_urls = [
            "https://www.morgancapitalgroup.com/open-account",
            "https://www.morgancapitalgroup.com/signup",
            "https://www.morgancapitalgroup.com/register",
            "https://www.morgancapitalgroup.com/app",
            "https://www.morgancapitalgroup.com/dashboard",
            "https://www.morgancapitalgroup.com/portfolio",
            "https://app.morgancapitalgroup.com",
            "https://app.morgancapitalgroup.com/login",
            "https://portal.morgancapitalgroup.com",
            "https://client.morgancapitalgroup.com",
            "https://www.morgancapitalgroup.com/api",
        ]
        for url in modern_urls:
            try:
                r = await page.request.get(url)
                t = await r.text()
                print("MODERN", r.status, url, t[:100].replace("\n", " "))
                out.setdefault("modern", []).append(
                    {"url": url, "status": r.status, "ct": r.headers.get("content-type"), "head": t[:300]}
                )
            except Exception as exc:
                out.setdefault("modern", []).append({"url": url, "error": str(exc)})

        # Scan homepage / _next for auth API hosts
        await page.goto("https://www.morgancapitalgroup.com/", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        scripts = await page.eval_on_selector_all("script[src]", "els => els.map(e => e.src)")
        api_hosts = set()
        api_paths = set()
        for src in scripts:
            if "/_next/" not in (src or ""):
                continue
            try:
                text = await (await page.request.get(src)).text()
            except Exception:
                continue
            for m in re.findall(r"https://[a-zA-Z0-9.-]*morgan[a-zA-Z0-9.-]*", text):
                api_hosts.add(m)
            for m in re.findall(r'["\`](/api/[a-zA-Z0-9_\-/?=&%]+)["\`]', text):
                api_paths.add(m)
            for m in re.findall(r'["\`](https://[^"\']+/api/[^"\']+)["\`]', text):
                api_paths.add(m)
            for key in ("login", "signin", "auth", "wallet", "portfolio", "order"):
                if key in text.lower():
                    for mm in re.finditer(key, text, re.I):
                        api_paths.add(text[max(0, mm.start() - 40) : mm.start() + 80])
                        break
        out["modern_hosts"] = sorted(api_hosts)
        out["modern_api_clues"] = sorted(api_paths)[:100]
        print("HOSTS", out["modern_hosts"])
        print("CLUES", len(out["modern_api_clues"]))

        # Click Open an account and inspect
        try:
            await page.goto("https://www.morgancapitalgroup.com/", wait_until="domcontentloaded")
            loc = page.locator('a:has-text("Open an account"), button:has-text("Open an account")').first
            if await loc.count():
                await loc.click()
                await page.wait_for_timeout(3000)
                out["open_account_url"] = page.url
                out["open_account_inputs"] = await page.eval_on_selector_all(
                    "input",
                    "els => els.map(e => ({type:e.type,name:e.name,id:e.id,placeholder:e.placeholder}))",
                )
                print("OPEN ACCOUNT", page.url, out["open_account_inputs"])
        except Exception as exc:
            out["notes"].append(f"open account: {exc}")

        await browser.close()

    dest = ROOT / "logs" / "morgan_auth_discovery.json"
    dest.write_text(json.dumps(out, indent=2, default=str)[:900000], encoding="utf-8")
    print("Wrote", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
