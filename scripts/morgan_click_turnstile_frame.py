#!/usr/bin/env python3
"""Click Turnstile checkbox inside OOPIF on the live CDP Chrome session."""
from __future__ import annotations

import asyncio
import json
from playwright.async_api import async_playwright


async def main() -> None:
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp("http://127.0.0.1:9333")
        page = b.contexts[0].pages[0]
        frames = page.frames
        ts_frames = [f for f in frames if "turnstile" in f.url or "challenges.cloudflare" in f.url]
        print("ts_frames", len(ts_frames))
        for f in ts_frames:
            print("frame", f.url[:160])
            try:
                body = await f.locator("body").inner_text(timeout=3000)
                print("body_text", body[:300])
            except Exception as exc:
                print("body_err", exc)
            for sel in (
                'input[type="checkbox"]',
                '[role="checkbox"]',
                "label",
                "#success",
                ".cb-lb",
                "body",
            ):
                try:
                    loc = f.locator(sel).first
                    await loc.click(timeout=2000, force=True)
                    print("clicked", sel)
                    break
                except Exception as exc:
                    print("fail", sel, type(exc).__name__)
        await asyncio.sleep(3)
        st = await page.evaluate(
            """() => {
              const tok = document.querySelector('input[name=cf-turnstile-response]');
              const btn = document.querySelector('button[type=submit]');
              let resp = '';
              try { resp = turnstile.getResponse() || ''; } catch (e) {}
              return {tokLen: tok?(tok.value||'').length:0, respLen: resp.length, btnDisabled: btn?btn.disabled:null};
            }"""
        )
        print(json.dumps(st))


if __name__ == "__main__":
    asyncio.run(main())
