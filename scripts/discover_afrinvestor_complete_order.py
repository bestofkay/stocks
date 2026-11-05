#!/usr/bin/env python3
"""Locate Afrinvestor complete-buy/sell API paths from JS bundles."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("DATABASE_URL", "mysql+pymysql://u:p@127.0.0.1:3306/portfolio")
os.environ.setdefault(
    "APP_ENCRYPTION_KEY",
    os.environ.get("APP_ENCRYPTION_KEY")
    or __import__("base64").b64encode(__import__("secrets").token_bytes(32)).decode(),
)
os.environ.setdefault("APP_SECRET_KEY", os.environ.get("APP_SECRET_KEY") or ("x" * 48))
os.environ.setdefault("PLAYWRIGHT_HEADLESS", "true")

from app.connectors.afrinvestor.connector import AfrinvestConnector  # noqa: E402

BASE = "https://afrinvestor.com"


async def main() -> int:
    user = os.environ.get("AFR_USER", "").strip()
    password = os.environ.get("AFR_PASS", "")
    if not user or not password:
        print("Set AFR_USER and AFR_PASS", file=sys.stderr)
        return 1

    connector = AfrinvestConnector()
    posts: set[str] = set()
    snippets: list[str] = []
    try:
        await connector.authenticate({"username": user, "password": password})
        page = connector._page
        assert page is not None
        await page.goto(f"{BASE}/invest/equities/ACCESSCORP", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        scripts = await page.eval_on_selector_all("script[src]", "els => els.map(e => e.src)")
        for src in scripts:
            if "/_next/static/" not in src:
                continue
            text = await (await page.request.get(src)).text()
            for m in re.findall(r'post\([`"](/all/[^`"\']+)[`"]', text):
                posts.add(m)
            for key in (
                "CCompleteBuyOrder",
                "CCompleteSellOrder",
                "CInitiateBuyOrder",
                "CInitiateSellOrder",
                "complete_buy",
                "complete_sell",
            ):
                idx = text.find(key)
                if idx >= 0:
                    snippets.append(f"{key} @ {src.split('/')[-1]}: {text[idx:idx+400]}")
    finally:
        await connector.disconnect()

    out = {"posts": sorted(posts), "snippets": snippets}
    dest = ROOT / "logs" / "afrinvestor_complete_order_discovery.json"
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("POSTS:")
    for p in sorted(posts):
        print(p)
    print("--- SNIPPETS ---")
    for s in snippets:
        print(s)
        print()
    print("Wrote", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
