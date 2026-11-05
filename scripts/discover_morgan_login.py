#!/usr/bin/env python3
"""Focused Morgan Capital login discovery."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def dump_inputs(page):
    return await page.eval_on_selector_all(
        "input,button,a,select,textarea",
        """els => els.slice(0,120).map(e => ({
            tag: e.tagName, type: e.type||null, name: e.name||null, id: e.id||null,
            placeholder: e.placeholder||null, href: e.href||null,
            text: ((e.innerText||e.value||'')+'').trim().slice(0,100),
            visible: !!(e.offsetWidth||e.offsetHeight||e.getClientRects().length)
        }))""",
    )


async def main() -> int:
    user = os.environ.get("MORGAN_USER", "").strip()
    password = os.environ.get("MORGAN_PASS", "")
    if not user or not password:
        print("Set MORGAN_USER / MORGAN_PASS", file=sys.stderr)
        return 1

    from playwright.async_api import async_playwright

    out: dict = {"attempts": [], "network": []}
    seen = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()
        page.set_default_timeout(90000)

        async def on_req(req):
            u = req.url
            if "morgan" not in u.lower():
                return
            if req.method in {"POST", "PUT", "PATCH"} or "/api" in u or "ajax" in u.lower():
                key = f"{req.method} {u}"
                if key in seen:
                    return
                seen.add(key)
                out["network"].append({"method": req.method, "url": u, "post": (req.post_data or "")[:800]})

        async def on_resp(resp):
            u = resp.url
            if "morgan" not in u.lower():
                return
            ct = resp.headers.get("content-type", "")
            if "json" in ct or resp.request.method in {"POST", "PUT"}:
                try:
                    text = await resp.text()
                except Exception:
                    text = ""
                out.setdefault("responses", []).append(
                    {"status": resp.status, "url": u, "method": resp.request.method, "body": text[:1500]}
                )

        page.on("request", on_req)
        page.on("response", on_resp)

        urls = [
            "https://itrade.morgancapitalgroup.com/customers/",
            "https://www.morgancapitalgroup.com/itrade",
            "https://www.morgancapitalgroup.com/accounts",
        ]
        for url in urls:
            att: dict = {"start_url": url}
            try:
                await page.goto(url, wait_until="networkidle", timeout=90000)
            except Exception as exc:
                att["goto_error"] = str(exc)
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                except Exception as exc2:
                    att["goto_error2"] = str(exc2)
                    out["attempts"].append(att)
                    continue
            await page.wait_for_timeout(3000)
            att["final_url"] = page.url
            att["title"] = await page.title()
            att["inputs"] = await dump_inputs(page)
            # click login-looking links
            for sel in [
                'a:has-text("Login")',
                'a:has-text("Sign in")',
                'a:has-text("CLIENT LOGIN")',
                'button:has-text("Login")',
                'button:has-text("Sign in")',
                'text=/login to trade/i',
                'a:has-text("iTrade")',
                'text=/Sign in/i',
            ]:
                loc = page.locator(sel).first
                try:
                    if await page.locator(sel).count() and await loc.is_visible():
                        await loc.click(timeout=3000)
                        await page.wait_for_timeout(2500)
                        att.setdefault("clicks", []).append({"sel": sel, "url": page.url})
                except Exception:
                    continue
            att["after_click_url"] = page.url
            att["after_click_inputs"] = await dump_inputs(page)

            # frames?
            frames_info = []
            for fr in page.frames:
                frames_info.append({"url": fr.url, "name": fr.name})
            att["frames"] = frames_info

            # Try login in main + frames
            targets = [page] + [f for f in page.frames if f != page.main_frame]
            for target in targets:
                try:
                    inputs = await target.eval_on_selector_all(
                        "input",
                        "els => els.map(e => ({type:e.type,name:e.name,id:e.id,placeholder:e.placeholder}))",
                    )
                except Exception:
                    continue
                if not any((i.get("type") == "password") for i in inputs):
                    continue
                # find username + password
                user_filled = False
                for sel in [
                    'input[name="username"]',
                    'input[name="userName"]',
                    'input[name="UserName"]',
                    'input[name="login"]',
                    'input[name="email"]',
                    'input[id="username"]',
                    'input[id="UserName"]',
                    'input[type="text"]',
                    'input[type="email"]',
                ]:
                    try:
                        loc = target.locator(sel).first
                        if await target.locator(sel).count():
                            await loc.fill(user)
                            user_filled = True
                            att["filled_user_sel"] = sel
                            break
                    except Exception:
                        continue
                try:
                    pwd = target.locator('input[type="password"]').first
                    await pwd.fill(password)
                    att["filled_password"] = True
                except Exception as exc:
                    att["password_error"] = str(exc)
                    continue
                # submit
                submitted = False
                for sel in [
                    'button[type="submit"]',
                    'input[type="submit"]',
                    'button:has-text("Login")',
                    'button:has-text("Sign in")',
                    'input[value*="Login" i]',
                    'a:has-text("Login")',
                ]:
                    try:
                        if await target.locator(sel).count():
                            await target.locator(sel).first.click()
                            submitted = True
                            att["submit_sel"] = sel
                            break
                    except Exception:
                        continue
                if not submitted:
                    try:
                        await target.locator('input[type="password"]').first.press("Enter")
                        submitted = True
                        att["submit_sel"] = "Enter"
                    except Exception:
                        pass
                await page.wait_for_timeout(6000)
                att["post_login_url"] = page.url
                att["post_login_title"] = await page.title()
                att["user_filled"] = user_filled
                # content cues
                html = await page.content()
                att["html_signals"] = {
                    "has_portfolio": bool(re.search(r"portfolio|holdings|wallet|CSCS|order", html, re.I)),
                    "has_error": bool(re.search(r"invalid|incorrect|failed|error", html, re.I)),
                    "snippet": re.sub(r"\s+", " ", html)[:1500],
                }
                print("LOGIN", url, "->", page.url, "user_filled", user_filled, "submitted", submitted)
                break

            out["attempts"].append(att)

        await browser.close()

    dest = ROOT / "logs" / "morgan_login_discovery.json"
    dest.write_text(json.dumps(out, indent=2, default=str)[:500000], encoding="utf-8")
    print("Wrote", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
