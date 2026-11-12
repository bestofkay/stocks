#!/usr/bin/env python3
"""Refine InvestNaija order body until calculate-price succeeds."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.connectors.investnaija import api_paths as ap  # noqa: E402

OUT = ROOT / "logs" / "investnaija_order_probe2.json"


def encrypt_password(pw: str) -> str:
    der = base64.b64decode(ap.PASSWORD_PUBLIC_KEY_B64)
    key = serialization.load_der_public_key(der)
    ct = key.encrypt(
        pw.encode("utf-8"),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return base64.b64encode(ct).decode("ascii")


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: (v[:10] + "...[REDACTED]" if isinstance(v, str) and any(x in k.lower() for x in ("token", "signature", "password")) else redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


async def main() -> int:
    email = os.environ["IN_USER"].strip()
    password = os.environ["IN_PASS"]
    live = os.environ.get("LIVE_ORDER", "").strip().lower() in {"1", "true", "yes"}
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": ap.APP_ORIGIN,
        "Referer": ap.APP_ORIGIN + "/",
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json",
    }
    results: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        login = (await client.post(ap.LOGIN, json={"email": email, "password": encrypt_password(password), "rememberMe": True})).json()
        auth = {**headers, "Authorization": login["token"], "x-uuid-token": login["uuid_token"]}
        p = (await client.get(ap.TRADE_PORTFOLIOS, headers=auth)).json()["data"][0]
        pid, sig = p["id"], p["signature"]
        secure = {**auth, "x-secure-token": sig}
        day_term = next(t for t in (await client.get(ap.ORDER_TERMS, headers=auth)).json()["data"]["result"] if "DAY" in t["label"].upper())
        secs = {s.get("symbol"): s for s in (await client.get(ap.SECURITIES, headers=auth)).json()["data"]}
        buy = secs.get("JAIZBANK") or next(iter(secs.values()))
        hold = (p.get("portfolioHoldings") or [None])[0]
        px = float(buy.get("price") or buy.get("lastPx") or 1)

        qty_keys = [
            "quantityRequested",
            "qtyRequested",
            "requestedQuantity",
            "requestedQty",
            "Quantity Requested",
            "quantity_requested",
            "orderQuantity",
            "orderQty",
            "qty",
            "units",
            "volume",
        ]
        type_vals = ["LIMIT", "Limit", "limit", "LMT", "MARKET", "Market", "MKT"]
        type_keys = ["orderType", "type", "ordType", "Order Type", "order_type"]

        base = {
            "portfolioId": pid,
            "securityId": buy["id"],
            "symbol": buy.get("symbol"),
            "side": "BUY",
            "limitPrice": px,
            "price": px,
            "orderTermId": day_term["id"],
            "orderTerm": day_term["name"],
            "timeInForce": day_term.get("fixTimeInForce"),
        }

        # First find quantity key
        qty_key_found = None
        for qk in qty_keys:
            body = {**base, qk: 1, "orderType": "LIMIT"}
            rr = await client.post(ap.CALCULATE_PRICE, headers=secure, json=body)
            jb = rr.json()
            msg = str(jb)
            results.append({"step": f"qty:{qk}", "status": rr.status_code, "msg": msg[:200]})
            print("qty", qk, rr.status_code, msg[:120])
            if rr.status_code == 200 or (rr.status_code == 400 and "Quantity Requested" not in msg and "quantity" not in msg.lower()):
                qty_key_found = qk
                break
            if rr.status_code == 400 and "Order Type" in msg:
                qty_key_found = qk
                break

        if not qty_key_found:
            print("no qty key")
            OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
            return 1
        print("qty_key", qty_key_found)

        # Find order type key/value
        winning = None
        for tk in type_keys:
            for tv in type_vals:
                body = {**base, qty_key_found: 1, tk: tv}
                rr = await client.post(ap.CALCULATE_PRICE, headers=secure, json=body)
                jb = rr.json()
                msg = str(jb)
                results.append({"step": f"type:{tk}={tv}", "status": rr.status_code, "msg": msg[:250]})
                print("type", tk, tv, rr.status_code, msg[:140])
                if rr.status_code == 200:
                    winning = body
                    break
            if winning:
                break

        # Also try without price fields / with market
        if not winning:
            for tv in ("LIMIT", "MARKET"):
                body = {
                    "portfolioId": pid,
                    "securityId": buy["id"],
                    qty_key_found: 1,
                    "orderType": tv,
                    "limitPrice": px,
                    "orderTermId": day_term["id"],
                    "side": "BUY",
                }
                rr = await client.post(ap.CALCULATE_PRICE, headers=secure, json=body)
                jb = rr.json()
                print("slim", tv, rr.status_code, str(jb)[:160])
                results.append({"step": f"slim:{tv}", "status": rr.status_code, "req": body, "resp": redact(jb)})
                if rr.status_code == 200:
                    winning = body
                    break

        # Broader field discovery using error messages as guide
        if not winning:
            # maybe security uses secId string, side uses Buy/buy, etc.
            combos = [
                {**base, qty_key_found: 1, "orderType": "LIMIT", "secId": buy.get("secId"), "securityExchange": "NGX"},
                {**base, qty_key_found: 1, "orderType": "LIMIT", "board": "EQTY"},
                {**base, qty_key_found: 1, "orderType": "LIMIT", "marketCode": "NGX"},
                {**base, qty_key_found: 1, "orderType": "LIMIT", "orderSide": "BUY"},
                {**base, qty_key_found: 1, "orderType": "LIMIT", "buySell": "BUY"},
                {**base, qty_key_found: 1, "orderType": "LIMIT", "action": "BUY"},
                {
                    "portfolioId": pid,
                    "securityId": buy["id"],
                    qty_key_found: 1,
                    "orderType": "LIMIT",
                    "limitPrice": px,
                    "orderTermId": day_term["id"],
                    "orderTermName": day_term["name"],
                    "side": "BUY",
                    "currency": "NGN",
                },
            ]
            for i, body in enumerate(combos):
                rr = await client.post(ap.CALCULATE_PRICE, headers=secure, json=body)
                jb = rr.json()
                print("combo", i, rr.status_code, str(jb)[:160])
                results.append({"step": f"combo{i}", "status": rr.status_code, "req": body, "resp": redact(jb)})
                if rr.status_code == 200:
                    winning = body
                    break

        if winning:
            print("WINNING CALC", json.dumps({k: winning[k] for k in winning if k != "portfolioId"}))
            results.append({"step": "winning_calc", "req": winning})
            if live:
                rr = await client.post(ap.CREATE_ORDER, headers=secure, json=winning)
                jb = rr.json()
                print("CREATE_BUY", rr.status_code, str(jb)[:300])
                results.append({"step": "CREATE_BUY", "status": rr.status_code, "req": winning, "resp": redact(jb)})
                if hold and float(hold.get("quantity") or 0) >= 1:
                    sell = dict(winning)
                    sell["side"] = "SELL"
                    if "orderSide" in sell:
                        sell["orderSide"] = "SELL"
                    if "buySell" in sell:
                        sell["buySell"] = "SELL"
                    sell["securityId"] = hold["securityId"]
                    if "symbol" in sell:
                        sell["symbol"] = hold.get("symbol")
                    if "secId" in sell:
                        sell["secId"] = hold.get("symbol")
                    sell[qty_key_found] = 1
                    spx = float(hold.get("marketPrice") or 1)
                    if "limitPrice" in sell:
                        sell["limitPrice"] = spx
                    if "price" in sell:
                        sell["price"] = spx
                    rr = await client.post(ap.CREATE_ORDER, headers=secure, json=sell)
                    jb = rr.json()
                    print("CREATE_SELL", rr.status_code, str(jb)[:300])
                    results.append({"step": "CREATE_SELL", "status": rr.status_code, "req": sell, "resp": redact(jb)})
        else:
            print("no winning calculate body yet")

    OUT.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
