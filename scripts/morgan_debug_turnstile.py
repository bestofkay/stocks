#!/usr/bin/env python3
"""Debug: open headed Chrome, dump Turnstile DOM state after load."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOGS = ROOT / "logs"
PROFILE = LOGS / "morgan_chrome_profile"
OUT = LOGS / "morgan_turnstile_debug.json"


async def main() -> int:
    user = os.environ["MORGAN_USER"].strip()
    password = os.environ["MORGAN_PASS"]
    from playwright.async_api import async_playwright

    PROFILE.mkdir(exist_ok=True)
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            channel="chrome",
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://www.morgancapitalgroup.com/sign-in", wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)
        await page.locator('input[type="email"]').fill(user)
        await page.locator('input[type="password"]').fill(password)
        await page.wait_for_timeout(8000)

        info = await page.evaluate(
            """() => {
              const ifr = [...document.querySelectorAll('iframe')].map(i => ({
                src: (i.src||'').slice(0,120), title: i.title, w: i.offsetWidth, h: i.offsetHeight
              }));
              const tok = document.querySelector('input[name="cf-turnstile-response"]');
              const btn = document.querySelector('button[type="submit"]');
              const host = document.querySelector('.mt-4.flex.justify-center');
              return {
                url: location.href,
                iframes: ifr,
                tokLen: tok ? (tok.value||'').length : -1,
                btnDisabled: btn ? btn.disabled : null,
                hostHtml: host ? host.innerHTML.slice(0,800) : null,
                turnstileType: typeof turnstile,
                scripts: [...document.scripts].map(s=>s.src).filter(Boolean).filter(s=>/turnstile|challenge/i.test(s))
              };
            }"""
        )
        # Wait up to 60s for iframe OR token, clicking bbox if possible
        for i in range(30):
            st = await page.evaluate(
                """() => {
                  const tok = document.querySelector('input[name="cf-turnstile-response"]');
                  const btn = document.querySelector('button[type="submit"]');
                  return {
                    tokLen: tok ? (tok.value||'').length : 0,
                    btnDisabled: btn ? btn.disabled : null,
                    iframes: document.querySelectorAll('iframe').length
                  };
                }"""
            )
            info["poll"] = info.get("poll", [])
            info["poll"].append(st)
            if st["tokLen"] > 20 or st["btnDisabled"] is False:
                info["ready"] = True
                break
            loc = page.locator('iframe[src*="challenges.cloudflare.com"]').first
            try:
                if await loc.count():
                    box = await loc.bounding_box()
                    if box:
                        await page.mouse.click(box["x"] + 26, box["y"] + box["height"] / 2)
                        info.setdefault("clicks", []).append(box)
            except Exception as exc:
                info.setdefault("click_errors", []).append(str(exc))
            await page.wait_for_timeout(2000)

        if info.get("ready"):
            await page.locator('button[type="submit"]').click()
            await page.wait_for_timeout(5000)
            info["after_submit_url"] = page.url
            if "/sign-in" not in page.url:
                await ctx.storage_state(path=str(LOGS / "morgan_storage_state.json"))
                info["storage_saved"] = True

        OUT.write_text(json.dumps(info, indent=2), encoding="utf-8")
        print(json.dumps({"ready": info.get("ready"), "url": info.get("after_submit_url", page.url), "out": str(OUT)}))
        # keep open briefly so user can see
        await page.wait_for_timeout(10000)
        await ctx.close()
        return 0 if info.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
