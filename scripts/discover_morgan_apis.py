#!/usr/bin/env python3
"""Discover Morgan Capital login + authenticated API surface (authorized account)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("PLAYWRIGHT_HEADLESS", "true")

BASES = [
    "https://itrade.morgancapitalgroup.com",
    "https://www.morgancapitalgroup.com",
    "https://morgancapitalgroup.com",
    "https://morgancapital.ng",
]


def _host_ok(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(
        host == d or host.endswith("." + d)
        for d in (
            "morgancapitalgroup.com",
            "morgancapital.ng",
            "www.morgancapitalgroup.com",
            "itrade.morgancapitalgroup.com",
        )
    )


def _redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(x in lk for x in ("password", "pin", "token", "secret", "bvn", "nin")):
                out[k] = "***"
            elif "account_number" in lk or "nuban" in lk:
                s = str(v)
                out[k] = (s[:2] + "***" + s[-2:]) if len(s) > 4 else "***"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


async def main() -> int:
    user = os.environ.get("MORGAN_USER", "").strip() or os.environ.get("AFR_USER", "").strip()
    password = os.environ.get("MORGAN_PASS", "") or os.environ.get("AFR_PASS", "")
    if not user or not password:
        print("Set MORGAN_USER and MORGAN_PASS", file=sys.stderr)
        return 1

    from playwright.async_api import async_playwright

    out: dict = {
        "started": datetime.now(timezone.utc).isoformat(),
        "pages": [],
        "login_attempts": [],
        "network": [],
        "notes": [],
    }
    seen: set[str] = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(60000)

        async def on_req(req):  # noqa: ANN001
            url = req.url
            if not _host_ok(url):
                return
            if not any(x in url.lower() for x in ("/api", "ajax", "json", "graphql", "rest", "service")):
                # still capture POSTs
                if req.method not in {"POST", "PUT", "PATCH"}:
                    return
            key = f"{req.method} {url}"
            if key in seen:
                return
            seen.add(key)
            entry = {
                "method": req.method,
                "url": url,
                "post": (req.post_data or "")[:600],
            }
            out["network"].append(entry)

        page.on("request", on_req)

        # Probe candidate pages
        candidates = [
            "https://itrade.morgancapitalgroup.com/customers/",
            "https://itrade.morgancapitalgroup.com/",
            "https://www.morgancapitalgroup.com/accounts",
            "https://www.morgancapitalgroup.com/login",
            "https://www.morgancapitalgroup.com/auth/login",
            "https://www.morgancapitalgroup.com/signin",
            "https://morgancapital.ng/login",
            "https://morgancapital.ng/auth",
            "https://www.morgancapitalgroup.com/itrade",
        ]
        for url in candidates:
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(1500)
                title = await page.title()
                has_email = await page.locator('input[type="email"], input[name*="user" i], input[name*="email" i], input[name*="login" i]').count()
                has_pass = await page.locator('input[type="password"]').count()
                entry = {
                    "url": url,
                    "final_url": page.url,
                    "status": resp.status if resp else None,
                    "title": title,
                    "email_like_inputs": has_email,
                    "password_inputs": has_pass,
                }
                out["pages"].append(entry)
                print("PAGE", entry)
            except Exception as exc:
                out["pages"].append({"url": url, "error": str(exc)})
                print("PAGE FAIL", url, exc)

        # Try classic iTrade login
        login_url = "https://itrade.morgancapitalgroup.com/customers/"
        await page.goto(login_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        # Dump form fields
        fields = await page.eval_on_selector_all(
            "input,button,select",
            """els => els.map(e => ({
                tag: e.tagName, type: e.type||null, name: e.name||null, id: e.id||null,
                placeholder: e.placeholder||null, text: (e.innerText||'').slice(0,80)
            }))""",
        )
        out["login_form_fields"] = fields
        print("FIELDS", json.dumps(fields, indent=2)[:2000])

        # Attempt fill common patterns
        user_selectors = [
            'input[name="username"]',
            'input[name="user"]',
            'input[name="login"]',
            'input[name="email"]',
            'input[type="email"]',
            'input[id*="user" i]',
            'input[id*="login" i]',
            'input[placeholder*="user" i]',
            'input[placeholder*="email" i]',
            'input[type="text"]',
        ]
        pass_selectors = [
            'input[type="password"]',
            'input[name="password"]',
            'input[name="passwd"]',
        ]
        submit_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Login")',
            'button:has-text("Sign in")',
            'button:has-text("Log in")',
            'a:has-text("Login")',
        ]

        async def first_visible(sels):
            for sel in sels:
                loc = page.locator(sel).first
                try:
                    if await page.locator(sel).count() > 0 and await loc.is_visible():
                        return loc, sel
                except Exception:
                    continue
            return None, None

        u_el, u_sel = await first_visible(user_selectors)
        p_el, p_sel = await first_visible(pass_selectors)
        s_el, s_sel = await first_visible(submit_selectors)
        attempt = {"user_sel": u_sel, "pass_sel": p_sel, "submit_sel": s_sel, "url": page.url}
        if u_el and p_el:
            await u_el.fill(user)
            await p_el.fill(password)
            if s_el:
                await s_el.click()
            else:
                await p_el.press("Enter")
            await page.wait_for_timeout(5000)
            attempt["after_url"] = page.url
            attempt["title"] = await page.title()
            # Capture HTML snippet
            html = await page.content()
            attempt["html_head"] = html[:2500]
            # Look for API-ish links in page
            api_like = sorted(set(re.findall(r'https?://[^"\']+|/[a-zA-Z0-9_\-/]*(?:api|portfolio|wallet|order|market)[a-zA-Z0-9_\-/]*', html)))[:80]
            attempt["api_like"] = api_like
            print("LOGIN attempt ->", page.url, attempt["title"])
        else:
            attempt["error"] = "login fields not found"
            print("LOGIN fields missing")
        out["login_attempts"].append(attempt)

        # If still on marketing site accounts flow, try that too
        for url in [
            "https://www.morgancapitalgroup.com/accounts",
            "https://www.morgancapitalgroup.com/login",
        ]:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)
                u_el, u_sel = await first_visible(user_selectors)
                p_el, p_sel = await first_visible(pass_selectors)
                s_el, s_sel = await first_visible(submit_selectors)
                att = {"url": url, "final": page.url, "user_sel": u_sel, "pass_sel": p_sel}
                if u_el and p_el:
                    await u_el.fill(user)
                    await p_el.fill(password)
                    if s_el:
                        await s_el.click()
                    else:
                        await p_el.press("Enter")
                    await page.wait_for_timeout(5000)
                    att["after_url"] = page.url
                    att["title"] = await page.title()
                    att["html_head"] = (await page.content())[:2000]
                    print("ALT LOGIN", url, "->", page.url)
                out["login_attempts"].append(att)
            except Exception as exc:
                out["login_attempts"].append({"url": url, "error": str(exc)})

        # Scan scripts on current page for API paths
        try:
            scripts = await page.eval_on_selector_all("script[src]", "els => els.map(e => e.src)")
            api_hits: set[str] = set()
            for src in scripts[:40]:
                if not src or not _host_ok(src):
                    continue
                try:
                    text = await (await page.request.get(src)).text()
                except Exception:
                    continue
                for m in re.findall(r"/[a-zA-Z0-9_\-/]*(?:api|portfolio|wallet|order|market|equity|holding|account)[a-zA-Z0-9_\-/?=&%]*", text):
                    if len(m) < 120:
                        api_hits.add(m)
            out["js_api_hits"] = sorted(api_hits)[:200]
            print("JS hits", len(api_hits))
            for h in sorted(api_hits)[:40]:
                print(h)
        except Exception as exc:
            out["notes"].append(f"js scan: {exc}")

        await browser.close()

    dest = ROOT / "logs" / "morgan_api_discovery.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(_redact(out), indent=2, default=str), encoding="utf-8")
    print("Wrote", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
