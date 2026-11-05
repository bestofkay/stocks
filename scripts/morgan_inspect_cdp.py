#!/usr/bin/env python3
import asyncio
import json
from playwright.async_api import async_playwright


async def main() -> None:
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp("http://127.0.0.1:9333")
        page = b.contexts[0].pages[0]
        info = await page.evaluate(
            """() => ({
              url: location.href,
              iframes: [...document.querySelectorAll('iframe')].map(i => ({
                src: i.src, w: i.offsetWidth, h: i.offsetHeight, id: i.id
              })),
              host: document.querySelector('.mt-4.flex.justify-center')?.innerHTML,
              scripts: [...document.scripts].filter(s => /turnstile|challenge/i.test(s.src)).map(s => s.src),
              turnstile: typeof turnstile,
              btnDisabled: document.querySelector('button[type=submit]')?.disabled,
              email: document.querySelector('input[type=email]')?.value
            })"""
        )
        print(json.dumps({"info": info, "frames": [f.url for f in page.frames]}, indent=2)[:4000])


if __name__ == "__main__":
    asyncio.run(main())
