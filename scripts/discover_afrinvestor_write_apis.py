#!/usr/bin/env python3
"""Discover Afrinvestor deposit-VA and trade-related API paths (authorized account).

Credentials: AFR_USER / AFR_PASS. Does NOT place orders — GET/OPTIONS probes + SPA
network capture on funding/trade pages only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
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
os.environ.setdefault(
    "APP_SECRET_KEY",
    os.environ.get("APP_SECRET_KEY") or ("x" * 48),
)
os.environ.setdefault("PLAYWRIGHT_HEADLESS", "true")

from app.connectors.afrinvestor.connector import AfrinvestConnector  # noqa: E402

BASE = "https://afrinvestor.com"

GET_CANDIDATES = [
    "/api/all/wallet/dedicated",
    "/api/all/wallet/funding",
    "/api/all/wallet/virtual-account",
    "/api/all/wallet/deposit",
    "/api/all/wallet/deposits",
    "/api/all/wallet/accounts",
    "/api/all/wallet/bank",
    "/api/all/user-info",
    "/api/all/orders",
    "/api/all/orders/pending",
    "/api/all/orders/history",
    "/api/all/invest/orders",
    "/api/all/invest/equities/orders",
    "/api/all/invest/equities/order",
    "/api/all/trade/orders",
    "/api/all/trading/orders",
]

PAGE_PATHS = [
    "/wallet",
    "/wallet/fund",
    "/wallet/deposit",
    "/fund",
    "/deposit",
    "/trade",
    "/invest",
    "/invest/equities",
    "/markets",
]


def _redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(x in lk for x in ("bvn", "nin", "password", "pin", "token", "secret")):
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
    user = os.environ.get("AFR_USER", "").strip()
    password = os.environ.get("AFR_PASS", "")
    if not user or not password:
        print("Set AFR_USER and AFR_PASS", file=sys.stderr)
        return 1

    out: dict = {
        "started": datetime.now(timezone.utc).isoformat(),
        "get_probes": [],
        "network_while_browsing": [],
        "notes": [],
    }
    seen_net: set[str] = set()

    connector = AfrinvestConnector()
    try:
        auth = await connector.authenticate({"username": user, "password": password})
        if not auth.success:
            print(f"Auth failed: {auth.status} {auth.message}", file=sys.stderr)
            return 1
        page = connector._page
        assert page is not None

        async def on_request(request):  # noqa: ANN001
            url = request.url
            if "/api/" not in url:
                return
            key = f"{request.method} {url}"
            if key in seen_net:
                return
            seen_net.add(key)
            entry = {
                "method": request.method,
                "url": url,
                "resource_type": request.resource_type,
            }
            try:
                if request.method in {"POST", "PUT", "PATCH"} and request.post_data:
                    entry["post_data_preview"] = request.post_data[:500]
            except Exception:
                pass
            out["network_while_browsing"].append(entry)

        page.on("request", on_request)

        for path in GET_CANDIDATES:
            url = BASE + path
            resp = await page.request.get(url)
            text = await resp.text()
            ct = resp.headers.get("content-type", "")
            is_json = "json" in ct or (text[:1] in "{[")
            body = None
            if is_json and resp.status < 400:
                try:
                    body = _redact(json.loads(text))
                except Exception:
                    body = text[:300]
            out["get_probes"].append(
                {
                    "path": path,
                    "status": resp.status,
                    "content_type": ct,
                    "len": len(text),
                    "body": body if resp.status < 400 and is_json else None,
                    "head": None if (resp.status < 400 and is_json) else text[:120],
                }
            )
            print(f"GET {path} -> {resp.status}")

        # Browse SPA pages to capture trade/funding XHRs (no order submit).
        for path in PAGE_PATHS:
            try:
                await page.goto(BASE + path, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(2500)
                # Click common funding / buy affordances without confirming orders.
                for sel in [
                    'text=/fund|deposit|add money|unique account|virtual/i',
                    'text=/buy|trade|sell/i',
                    'a[href*="fund"]',
                    'a[href*="deposit"]',
                    'a[href*="trade"]',
                    'button:has-text("Buy")',
                    'button:has-text("Fund")',
                ]:
                    loc = page.locator(sel).first
                    try:
                        if await loc.count() > 0 and await loc.is_visible():
                            await loc.click(timeout=1500)
                            await page.wait_for_timeout(1500)
                    except Exception:
                        continue
                print(f"browsed {path}")
            except Exception as exc:
                out["notes"].append(f"browse {path}: {exc}")
                print(f"browse fail {path}: {exc}")

        # Portfolio-scoped order candidates if we can list a portfolio id
        try:
            plist = await page.request.get(f"{BASE}/api/all/portfolio/list")
            data = await plist.json()
            pid = None
            if isinstance(data, list) and data:
                pid = data[0].get("id") or data[0].get("portfolio_id")
            elif isinstance(data, dict):
                items = data.get("data") or data.get("portfolios") or data.get("items") or []
                if items:
                    pid = items[0].get("id") or items[0].get("portfolio_id")
            if pid:
                scoped = [
                    f"/api/all/portfolio/assets/{pid}/equity/orders",
                    f"/api/all/portfolio/assets/{pid}/orders",
                    f"/api/all/portfolio/{pid}/orders",
                    f"/api/all/invest/equities/orders?portfolioId={pid}",
                ]
                for path in scoped:
                    resp = await page.request.get(BASE + path)
                    text = await resp.text()
                    ct = resp.headers.get("content-type", "")
                    is_json = "json" in ct or (text[:1] in "{[")
                    body = None
                    if is_json and resp.status < 400:
                        try:
                            body = _redact(json.loads(text))
                        except Exception:
                            body = text[:300]
                    out["get_probes"].append(
                        {
                            "path": path,
                            "status": resp.status,
                            "content_type": ct,
                            "len": len(text),
                            "body": body if resp.status < 400 and is_json else None,
                            "head": None if (resp.status < 400 and is_json) else text[:120],
                        }
                    )
                    print(f"GET {path} -> {resp.status}")
        except Exception as exc:
            out["notes"].append(f"portfolio scoped: {exc}")

    finally:
        await connector.disconnect()

    out["finished"] = datetime.now(timezone.utc).isoformat()
    dest = ROOT / "logs" / "afrinvestor_write_api_discovery.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
