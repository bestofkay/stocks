#!/usr/bin/env python3
"""Login to Morgan iTrade (folio WebTrader) and capture wa/ API traffic."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOGIN_URL = "https://itrade.morgancapitalgroup.com/customers/"


async def main() -> int:
    user = os.environ.get("MORGAN_USER", "").strip()
    password = os.environ.get("MORGAN_PASS", "")
    if not user or not password:
        print("Set MORGAN_USER / MORGAN_PASS", file=sys.stderr)
        return 1

    from playwright.async_api import async_playwright

    out: dict = {"network": [], "responses": [], "notes": []}
    seen = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(90000)

        async def on_req(req):
            u = req.url
            if "morgan" not in u.lower():
                return
            key = f"{req.method} {u}"
            if key in seen and req.method == "GET":
                return
            seen.add(key)
            if req.method != "GET" or "/wa/" in u or "api" in u.lower() or u.endswith(".ashx"):
                out["network"].append(
                    {
                        "method": req.method,
                        "url": u,
                        "post": (req.post_data or "")[:1000],
                        "headers": {
                            k: v
                            for k, v in req.headers.items()
                            if k.lower() in {"content-type", "x-requested-with", "referer"}
                        },
                    }
                )

        async def on_resp(resp):
            u = resp.url
            if "morgan" not in u.lower():
                return
            if "/wa/" in u or resp.request.method in {"POST", "PUT"} or "json" in resp.headers.get("content-type", ""):
                try:
                    text = await resp.text()
                except Exception:
                    text = ""
                out["responses"].append(
                    {
                        "status": resp.status,
                        "method": resp.request.method,
                        "url": u,
                        "ct": resp.headers.get("content-type"),
                        "body": text[:3000],
                    }
                )
                print("RESP", resp.status, resp.request.method, u, text[:120].replace("\n", " "))

        page.on("request", on_req)
        page.on("response", on_resp)

        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        # Wait for folio init
        try:
            await page.wait_for_function("() => window.folio !== undefined", timeout=15000)
            out["notes"].append("folio global present")
        except Exception:
            out["notes"].append("folio global missing")

        await page.fill("#un", user)
        await page.fill("#pw", password)
        await page.click("#btn")
        await page.wait_for_timeout(8000)

        out["after_login_url"] = page.url
        out["title"] = await page.title()
        html = await page.content()
        out["html_snippet"] = re.sub(r"\s+", " ", html)[:3000]
        # visible text errors
        body_text = await page.inner_text("body")
        out["body_text"] = body_text[:2000]
        print("URL", page.url)
        print("BODY", body_text[:500])

        # If still on login, try username without domain / alternate
        if "Customer Account" in body_text or await page.locator("#un").count():
            alts = []
            if "@" in user:
                alts.append(user.split("@")[0])
            for alt in alts:
                print("RETRY with", alt)
                await page.fill("#un", alt)
                await page.fill("#pw", password)
                await page.click("#btn")
                await page.wait_for_timeout(8000)
                out["retry_url"] = page.url
                out["retry_body"] = (await page.inner_text("body"))[:1500]
                print("RETRY BODY", out["retry_body"][:400])

        # If logged in, poke around
        if "/wa/" in page.url or "portfolio" in body_text.lower() or await page.locator("text=/portfolio|holdings|order/i").count():
            out["notes"].append("possible authenticated session")
            for sel in [
                'text=/Portfolio/i',
                'text=/Account/i',
                'text=/Order/i',
                'text=/Market/i',
                'text=/Report/i',
            ]:
                try:
                    if await page.locator(sel).count():
                        await page.locator(sel).first.click(timeout=2000)
                        await page.wait_for_timeout(2000)
                except Exception:
                    pass

        # List JS assets referencing wa/
        scripts = await page.eval_on_selector_all("script[src]", "els => els.map(e => e.src)")
        out["scripts"] = scripts
        for src in scripts:
            if not src:
                continue
            try:
                text = await (await page.request.get(src)).text()
            except Exception:
                continue
            if "wa/" in text or "login" in text.lower():
                hits = sorted(set(re.findall(r"wa/[A-Za-z0-9_./?\-]+", text)))
                out.setdefault("wa_paths", []).extend(hits[:50])
                # login function clues
                for m in re.finditer(r".{0,40}login.{0,80}", text, re.I):
                    out.setdefault("login_snippets", []).append(m.group(0)[:120])
                    if len(out["login_snippets"]) > 20:
                        break

        await browser.close()

    # unique wa paths
    if "wa_paths" in out:
        out["wa_paths"] = sorted(set(out["wa_paths"]))

    dest = ROOT / "logs" / "morgan_itrade_login.json"
    dest.write_text(json.dumps(out, indent=2, default=str)[:800000], encoding="utf-8")
    print("Wrote", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
