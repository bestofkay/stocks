#!/usr/bin/env python3
"""Discover Afrinvestor tradeable markets / equities listing APIs."""

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
MARKET_CANDIDATES = [
    "NGX",
    "NSE",
    "GSE",
    "GHSE",
    "USE",
    "NSEASI",
    "NASDAQ",
    "NYSE",
    "US",
    "KE",
    "GH",
    "NG",
]


async def main() -> int:
    user = os.environ.get("AFR_USER", "").strip()
    password = os.environ.get("AFR_PASS", "")
    if not user or not password:
        print("Set AFR_USER and AFR_PASS", file=sys.stderr)
        return 1

    out: dict = {"market_probes": {}, "js_hits": [], "ngx": {}}
    connector = AfrinvestConnector()
    try:
        auth = await connector.authenticate({"username": user, "password": password})
        if not auth.success:
            print("auth failed", auth.message)
            return 1
        page = connector._page
        assert page is not None

        await page.goto(f"{BASE}/invest/equities", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        scripts = await page.eval_on_selector_all("script[src]", "els => els.map(e => e.src)")
        hits: set[str] = set()
        for src in scripts:
            if "/_next/static/" not in src:
                continue
            text = await (await page.request.get(src)).text()
            for m in re.findall(r"/all/invest/[A-Za-z0-9_\-/?=&%{}]+", text):
                if any(k in m.lower() for k in ("market", "exchange", "equit")):
                    hits.add(m)
            for m in re.findall(r"marketId[=:][\s\"']*([A-Za-z0-9_\-]+)", text):
                hits.add(f"marketId={m}")
            for m in re.findall(r'["\']marketId["\']\s*:\s*["\']([A-Za-z0-9_\-]+)["\']', text):
                hits.add(f"marketId={m}")
        out["js_hits"] = sorted(hits)
        print("JS_HITS", len(hits))
        for h in sorted(hits):
            print(h)

        for mid in MARKET_CANDIDATES:
            url = f"{BASE}/api/all/invest/equities?marketId={mid}&per_page=5&page=1"
            resp = await page.request.get(url)
            text = await resp.text()
            entry: dict = {"status": resp.status}
            if resp.status == 200:
                try:
                    data = json.loads(text)
                except Exception:
                    data = None
                if isinstance(data, dict):
                    items = data.get("data") or []
                    entry["page_count"] = len(items) if isinstance(items, list) else 0
                    entry["meta"] = {
                        k: data.get(k)
                        for k in (
                            "total",
                            "per_page",
                            "current_page",
                            "last_page",
                            "from",
                            "to",
                            "success",
                            "message",
                        )
                        if k in data
                    }
                    if isinstance(items, list) and items:
                        first = items[0]
                        entry["first"] = {
                            k: first.get(k)
                            for k in (
                                "symbol",
                                "secId",
                                "secDesc",
                                "exchangeId",
                                "marketId",
                                "price",
                                "currency",
                            )
                            if isinstance(first, dict)
                        }
                print("OK", mid, entry.get("page_count"), entry.get("meta"))
            else:
                print("FAIL", mid, resp.status, text[:80].replace("\n", " "))
            out["market_probes"][mid] = entry

        # Paginate NGX to get full symbol list
        symbols: list[str] = []
        page_no = 1
        last_page = 1
        while page_no <= last_page and page_no <= 50:
            url = f"{BASE}/api/all/invest/equities?marketId=NGX&per_page=100&page={page_no}"
            resp = await page.request.get(url)
            data = await resp.json()
            items = data.get("data") if isinstance(data, dict) else []
            if isinstance(data, dict):
                last_page = int(data.get("last_page") or data.get("meta", {}).get("last_page") or page_no)
                if not data.get("last_page") and isinstance(data.get("meta"), dict):
                    last_page = int(data["meta"].get("last_page") or page_no)
            for item in items or []:
                sym = item.get("symbol") or item.get("secId")
                if sym:
                    symbols.append(str(sym))
            if not items:
                break
            page_no += 1

        out["ngx"] = {
            "symbol_count": len(symbols),
            "symbols": symbols,
            "pages_fetched": page_no - 1,
        }
        print("NGX symbols", len(symbols), "pages", page_no - 1)
        print("first 30", symbols[:30])
    finally:
        await connector.disconnect()

    dest = ROOT / "logs" / "afrinvestor_markets_discovery.json"
    dest.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print("Wrote", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
