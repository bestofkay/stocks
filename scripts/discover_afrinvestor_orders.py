#!/usr/bin/env python3
"""Find Afrinvestor equity order-create endpoint via JS + method probes (no filled trade)."""

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
# Portfolio id comes from env (never hardcode account identifiers)
PID = os.environ.get("AFR_PORTFOLIO_ID", "").strip()
KEYWORDS = ("order", "trade", "buy", "sell", "bid", "offer", "dedicated", "wallet", "oms")


async def main() -> int:
    user = os.environ.get("AFR_USER", "").strip()
    password = os.environ.get("AFR_PASS", "")
    if not user or not password:
        print("Set AFR_USER and AFR_PASS", file=sys.stderr)
        return 1
    if not PID:
        print("Set AFR_PORTFOLIO_ID (see GET /api/v1/portfolio/accounts)", file=sys.stderr)
        return 1

    out: dict = {"js_api_hits": [], "interesting_probes": [], "network": []}
    connector = AfrinvestConnector()
    try:
        auth = await connector.authenticate({"username": user, "password": password})
        if not auth.success:
            print("auth failed", auth.message)
            return 1
        page = connector._page
        assert page is not None

        async def on_req(req):  # noqa: ANN001
            if "/api/" in req.url:
                out["network"].append(
                    {
                        "method": req.method,
                        "url": req.url,
                        "post": (req.post_data or "")[:800],
                    }
                )

        page.on("request", on_req)

        await page.goto(f"{BASE}/invest/equities", wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        scripts = await page.eval_on_selector_all("script[src]", "els => els.map(e => e.src)")
        api_hits: set[str] = set()
        for src in scripts:
            if "/_next/static/" not in src:
                continue
            try:
                resp = await page.request.get(src)
                text = await resp.text()
            except Exception:
                continue
            for m in re.findall(r"/api/all/[A-Za-z0-9_\-/?=&%.{}]+", text):
                if any(k in m.lower() for k in KEYWORDS):
                    api_hits.add(m)
            for m in re.findall(r'["\'](/api/all/[^"\']+)', text):
                if any(k in m.lower() for k in KEYWORDS):
                    api_hits.add(m)
            # also catch template literals like `.../orders`
            for m in re.findall(r"`(/api/all/[^`]+)`", text):
                if any(k in m.lower() for k in KEYWORDS):
                    api_hits.add(m)
            # common fetch path fragments
            for m in re.findall(r'invest/equities/[A-Za-z0-9_\-/?=&]+', text):
                if any(k in m.lower() for k in KEYWORDS):
                    api_hits.add("/api/all/" + m)

        out["js_api_hits"] = sorted(api_hits)
        print("JS_API_HITS", len(api_hits))
        for h in sorted(api_hits):
            print(h)

        for path in [
            "/invest/equities/ACCESSCORP",
            f"/portfolio/{PID}",
            f"/portfolio/{PID}/orders",
        ]:
            try:
                await page.goto(BASE + path, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2500)
                print("PAGE", path, "->", page.url)
            except Exception as exc:
                print("PAGE FAIL", path, exc)

        paths = [
            "/api/all/invest/equities/orders",
            f"/api/all/invest/equities/orders?portfolioId={PID}",
            "/api/all/invest/equities/order",
            "/api/all/invest/equity/orders",
            "/api/all/invest/orders/equities",
            "/api/all/trading/equities/orders",
            "/api/all/invest/equities/place-order",
            "/api/all/invest/equities/place_order",
            "/api/all/invest/equities/create-order",
            "/api/all/brokerage/orders",
            "/api/all/oms/orders",
            "/api/all/fix/orders",
            "/api/all/invest/equities/bid",
            "/api/all/invest/equities/offer",
            "/api/all/invest/equities/market-order",
            "/api/all/invest/equities/limit-order",
            "/api/all/invest/equities/submit-order",
            "/api/all/invest/equities/new-order",
            "/api/all/wallet/dedicated",
        ]
        # Include any order-ish paths from JS
        for h in api_hits:
            if h.startswith("/api/"):
                paths.append(h.split("?")[0])

        body = {
            "portfolioId": PID,
            "secId": "ACCESSCORP",
            "side": "BUY",
            "requestedQty": 1,
            "tif": "DAY",
            "orderType": "MARKET",
        }
        data = json.dumps(body)
        seen = set()
        for path in paths:
            path = path.replace("${portfolioId}", PID).replace("{portfolioId}", PID)
            if path in seen:
                continue
            seen.add(path)
            for method in ("POST", "PUT", "PATCH"):
                if method == "POST":
                    r = await page.request.post(
                        BASE + path, data=data, headers={"content-type": "application/json"}
                    )
                elif method == "PUT":
                    r = await page.request.put(
                        BASE + path, data=data, headers={"content-type": "application/json"}
                    )
                else:
                    r = await page.request.fetch(
                        BASE + path,
                        method="PATCH",
                        data=data,
                        headers={"content-type": "application/json"},
                    )
                text = await r.text()
                ct = r.headers.get("content-type", "")
                interesting = r.status != 404 or "json" in ct
                if interesting:
                    entry = {
                        "method": method,
                        "path": path,
                        "status": r.status,
                        "body": text[:500],
                    }
                    out["interesting_probes"].append(entry)
                    print(method, path, "->", r.status, text[:180].replace("\n", " "))

    finally:
        await connector.disconnect()

    dest = ROOT / "logs" / "afrinvestor_order_discovery.json"
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("Wrote", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
