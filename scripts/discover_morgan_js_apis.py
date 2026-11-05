#!/usr/bin/env python3
"""Extract API hosts/paths from MorganCapital Next.js bundles."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main() -> int:
    from playwright.async_api import async_playwright

    hits: set[str] = set()
    clues: list[dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        for url in [
            "https://www.morgancapitalgroup.com/sign-in",
            "https://www.morgancapitalgroup.com/dashboard",
            "https://www.morgancapitalgroup.com/markets",
        ]:
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
            scripts = await page.eval_on_selector_all("script[src]", "els => els.map(e => e.src)")
            for src in scripts:
                if "/_next/static/" not in (src or ""):
                    continue
                try:
                    text = await (await page.request.get(src)).text()
                except Exception:
                    continue
                for m in re.findall(r"https://[a-zA-Z0-9._\-:/]+", text):
                    low = m.lower()
                    if any(
                        k in low
                        for k in (
                            "api",
                            "supabase",
                            "auth",
                            "firebase",
                            "clerk",
                            "cognito",
                            "morgan",
                            "turnstile",
                        )
                    ):
                        hits.add(m[:220])
                for m in re.findall(r'["\'](/api/[a-zA-Z0-9_\-/?=&%]+)["\']', text):
                    hits.add(m)
                for m in re.findall(r"`(/api/[^`]+)`", text):
                    hits.add(m)
                for key in (
                    "signIn",
                    "sign-in",
                    "login",
                    "password",
                    "turnstile",
                    "NEXT_PUBLIC",
                    "supabase",
                    "createClient",
                    "wallet",
                    "portfolio",
                    "placeOrder",
                    "dedicated",
                    "virtual",
                ):
                    idx = text.find(key)
                    if idx >= 0:
                        clues.append(
                            {
                                "key": key,
                                "chunk": src.split("/")[-1][:50],
                                "ctx": text[max(0, idx - 80) : idx + 160].replace("\n", " ")[:260],
                            }
                        )
        await browser.close()

    # de-dupe clues by key+ctx
    uniq = []
    seen = set()
    for c in clues:
        k = (c["key"], c["ctx"][:80])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(c)

    dest = ROOT / "logs" / "morgan_js_apis.json"
    dest.write_text(
        json.dumps({"hits": sorted(hits), "clues": uniq[:120]}, indent=2),
        encoding="utf-8",
    )
    print("HITS", len(hits))
    for h in sorted(hits):
        print(h)
    print("CLUES", len(uniq))
    for c in uniq[:30]:
        print(c["key"], ":", c["ctx"][:180])
    print("Wrote", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
