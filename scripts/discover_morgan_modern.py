#!/usr/bin/env python3
"""Login to modern MorganCapital (sign-in) and capture portfolio/trade APIs."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SIGN_IN = "https://www.morgancapitalgroup.com/sign-in"


def redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(x in lk for x in ("password", "token", "authorization", "pin", "secret", "bvn", "nin")):
                out[k] = "***"
            elif "account_number" in lk or "nuban" in lk:
                s = str(v)
                out[k] = (s[:2] + "***" + s[-2:]) if len(s) > 4 else "***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


async def main() -> int:
    user = os.environ["MORGAN_USER"].strip()
    password = os.environ["MORGAN_PASS"]

    from playwright.async_api import async_playwright

    out: dict = {"responses": [], "network_posts": [], "js_apis": [], "notes": []}
    seen = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(90000)

        async def on_req(req):
            u = req.url
            if "morgan" not in u.lower() and "supabase" not in u.lower() and "firebase" not in u.lower():
                if "amazonaws" not in u.lower() and "api." not in u.lower():
                    return
            if req.method in {"POST", "PUT", "PATCH", "DELETE"} or "/api" in u:
                key = f"{req.method} {u}"
                if key in seen:
                    return
                seen.add(key)
                out["network_posts"].append(
                    {
                        "method": req.method,
                        "url": u,
                        "post": (req.post_data or "")[:1200],
                    }
                )

        async def on_resp(resp):
            u = resp.url
            ct = resp.headers.get("content-type", "")
            interesting = (
                "/api" in u
                or "supabase" in u
                or "firebase" in u
                or resp.request.method in {"POST", "PUT", "PATCH"}
                or "json" in ct
            )
            if not interesting:
                return
            if "morgancapital" not in u and "supabase" not in u and "api." not in u:
                # still keep auth-looking posts
                if resp.request.method == "GET" and "json" not in ct:
                    return
            try:
                text = await resp.text()
            except Exception:
                text = ""
            # skip huge next payloads
            if "_next" in u and "json" not in ct:
                return
            body = text[:5000]
            try:
                parsed = json.loads(text) if text and text[:1] in "{[" else None
            except Exception:
                parsed = None
            entry = {
                "status": resp.status,
                "method": resp.request.method,
                "url": u,
                "ct": ct,
                "body": redact(parsed) if parsed is not None else body[:1500],
            }
            out["responses"].append(entry)
            print("RESP", resp.status, resp.request.method, u[:140])

        page.on("request", on_req)
        page.on("response", on_resp)

        await page.goto(SIGN_IN, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        inputs = await page.eval_on_selector_all(
            "input,button",
            "els => els.map(e => ({tag:e.tagName,type:e.type,name:e.name,id:e.id,placeholder:e.placeholder,text:(e.innerText||'').slice(0,60)}))",
        )
        out["sign_in_inputs"] = inputs
        print("INPUTS", json.dumps(inputs, indent=2)[:2000])

        # Fill login
        email = page.locator('input[type="email"], input[name="email"], input[placeholder*="email" i]').first
        pwd = page.locator('input[type="password"]').first
        await email.fill(user)
        await pwd.fill(password)
        for sel in [
            'button[type="submit"]',
            'button:has-text("Sign in")',
            'button:has-text("Log in")',
            'button:has-text("Login")',
            'button:has-text("Continue")',
        ]:
            if await page.locator(sel).count():
                await page.locator(sel).first.click()
                break
        await page.wait_for_timeout(8000)
        out["after_login_url"] = page.url
        out["after_login_text"] = (await page.inner_text("body"))[:2500]
        print("AFTER LOGIN", page.url)
        print(out["after_login_text"][:800])

        # Scan JS for API base URLs from sign-in / dashboard chunks
        scripts = await page.eval_on_selector_all("script[src]", "els => els.map(e => e.src)")
        apis = set()
        for src in scripts:
            if "/_next/static/" not in (src or ""):
                continue
            try:
                text = await (await page.request.get(src)).text()
            except Exception:
                continue
            for m in re.findall(r"https://[a-zA-Z0-9._\-]+(?:/[a-zA-Z0-9_\-./]*)?", text):
                if any(k in m.lower() for k in ("api", "supabase", "morgan", "auth", "firebase")):
                    apis.add(m.split('"')[0].split("'")[0][:180])
            for m in re.findall(r'["\`](/api/[a-zA-Z0-9_\-/?=&%]+)["\`]', text):
                apis.add(m)
            for m in re.findall(r'["\`]((?:https:)?//[^"\']*(?:wallet|portfolio|order|equity|market|holding|deposit|auth|login)[^"\']*)["\`]', text, re.I):
                apis.add(m[:200])
        out["js_apis"] = sorted(apis)[:200]
        print("JS APIS", len(apis))
        for a in sorted(apis)[:40]:
            print(a)

        # Navigate authenticated areas if logged in
        for path in [
            "/dashboard",
            "/portfolio",
            "/wallet",
            "/markets",
            "/offers",
            "/profile",
        ]:
            try:
                await page.goto("https://www.morgancapitalgroup.com" + path, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(3500)
                print("NAV", path, "->", page.url)
                out.setdefault("pages", []).append(
                    {
                        "path": path,
                        "url": page.url,
                        "text": (await page.inner_text("body"))[:1200],
                    }
                )
            except Exception as exc:
                out.setdefault("pages", []).append({"path": path, "error": str(exc)})

        await browser.close()

    dest = ROOT / "logs" / "morgan_modern_discovery.json"
    dest.write_text(json.dumps(redact(out), indent=2, default=str)[:1000000], encoding="utf-8")
    print("Wrote", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
