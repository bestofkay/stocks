#!/usr/bin/env python3
"""Brute-force the create-order field whose Joi label is 'Price'."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.connectors.investnaija import api_paths as ap  # noqa: E402

OUT = ROOT / "logs" / "investnaija_price_keys.json"


def encrypt_password(pw: str) -> str:
    der = base64.b64decode(ap.PASSWORD_PUBLIC_KEY_B64)
    key = serialization.load_der_public_key(der)
    ct = key.encrypt(
        pw.encode("utf-8"),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return base64.b64encode(ct).decode("ascii")


async def main() -> int:
    email = os.environ["IN_USER"].strip()
    password = os.environ["IN_PASS"]
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": ap.APP_ORIGIN,
        "Referer": ap.APP_ORIGIN + "/",
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json",
    }
    results = []
    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        login = (await client.post(ap.LOGIN, json={"email": email, "password": encrypt_password(password), "rememberMe": True})).json()
        auth = {**headers, "Authorization": login["token"], "x-uuid-token": login["uuid_token"]}
        p = (await client.get(ap.TRADE_PORTFOLIOS, headers=auth)).json()["data"][0]
        secure = {**auth, "x-secure-token": p["signature"]}
        day_term = next(t for t in (await client.get(ap.ORDER_TERMS, headers=auth)).json()["data"]["result"] if "DAY" in t["label"].upper())
        buy = next(s for s in (await client.get(ap.SECURITIES, headers=auth)).json()["data"] if s.get("symbol") == "JAIZBANK")
        px = float(buy["price"])

        base = {
            "portfolioId": p["id"],
            "securityId": buy["id"],
            "securityName": buy["secId"],
            "quantityRequested": 1,
            "orderType": "LIMIT",
            "priceType": "LIMIT",
            "orderTermId": day_term["id"],
            "orderTermName": day_term["name"],
            "side": "BUY",
        }

        keys = [
            "price", "Price", "limitPrice", "LimitPrice", "orderPrice", "OrderPrice",
            "requestedPrice", "unitPrice", "tradePrice", "execPrice", "fixPrice",
            "lmtPrice", "lmtPx", "limitPx", "orderPx", "px", "PX", "lastPx",
            "bidPx", "offerPx", "stopPx", "stopPrice", "maxPrice", "minPrice",
            "marketPrice", "limit", "Limit", "LIMIT", "value", "amount",
            "consideration", "orderValue", "orderAmount", "cashAmount",
            "priceAmount", "priceValue", "quotedPrice", "entryPrice",
            "buyPrice", "sellPrice", "targetPrice", "triggerPrice",
            "priceRequested", "requestedPx", "ordPx", "ordPrice",
            "priceLimit", "limitedPrice", "limit_price", "order_price",
            "unit_price", "trade_price", "PriceAmount", "priceObj",
            "pricing", "prx", "p", "pxValue", "limitValue",
            "cashOrderQty", "grossTradeAmt", "netMoney", "settlementAmount",
            "estimatedPrice", "indicativePrice", "referencePrice",
            "capPrice", "floorPrice", "pegPrice", "discretionPrice",
        ]
        values = [
            px,
            round(px, 2),
            f"{px:.2f}",
            {"currency": "NGN", "amount": px},
            {"amount": px, "currency": "NGN"},
            {"currency": "NGN", "amount": px, "value": px},
            int(round(px * 100)),  # kobo
            str(int(round(px * 100))),
        ]

        # First confirm baseline
        rr = await client.post(ap.CREATE_ORDER, headers=secure, json=base)
        base_msg = (rr.json().get("error") or {}).get("message")
        print("baseline", rr.status_code, base_msg)
        results.append({"baseline": base_msg})

        winning = None
        for k in keys:
            for vi, val in enumerate(values):
                body = {**base, k: val}
                rr = await client.post(ap.CREATE_ORDER, headers=secure, json=body)
                msg = (rr.json().get("error") or {}).get("message") if rr.content else rr.text
                if rr.status_code in (200, 201) or (isinstance(msg, str) and "Price is required" not in msg):
                    print("HIT", k, vi, rr.status_code, msg)
                    results.append({"hit": k, "val": val, "status": rr.status_code, "msg": msg})
                    if rr.status_code in (200, 201):
                        winning = body
                        break
                    # keep going for first progress per key
                    if "Price is required" not in str(msg):
                        # record and continue searching for 200
                        pass
                # don't print misses
            if winning:
                break

        if not winning:
            # maybe price must be combined with something else — try common Zanibal OMS field set from similar portals
            mega = {
                **base,
                "price": px,
                "limitPrice": px,
                "orderPrice": px,
                "requestedPrice": px,
                "unitPrice": px,
                "limitPx": px,
                "orderPx": px,
                "fixPrice": px,
                "amount": px,
                "value": px,
                "consideration": px,
                "symbol": buy["symbol"],
                "secId": buy["secId"],
                "marketCode": "NGX",
                "board": "EQTY",
                "currency": "NGN",
                "timeInForce": day_term.get("fixTimeInForce"),
                "orderTerm": day_term["name"],
                "orderTermLabel": day_term["label"],
                "orderSide": "BUY",
                "buySell": "BUY",
                "action": "BUY",
                "accountNo": p.get("accountNo"),
                "tradingAccountNo": p.get("tradingAccountNo"),
                "cashAccountId": p.get("cashAccountId"),
                "clientOrderId": "agg-test-1",
                "clOrdId": "agg-test-1",
                "reference": "agg-test-1",
                "notes": "test",
                "comment": "test",
                "allOrNone": False,
                "anonymous": False,
                "hidden": False,
                "isMarket": False,
                "isLimit": True,
            }
            rr = await client.post(ap.CREATE_ORDER, headers=secure, json=mega)
            msg = (rr.json().get("error") or {}).get("message")
            print("mega", rr.status_code, msg)
            results.append({"mega": {"status": rr.status_code, "msg": msg}})
            if rr.status_code in (200, 201):
                winning = mega
            elif msg and "Price is required" not in msg:
                print("mega progressed!")
                # continue iterative from mega
                body = mega
                for _ in range(15):
                    rr = await client.post(ap.CREATE_ORDER, headers=secure, json=body)
                    jb = rr.json()
                    msg = (jb.get("error") or {}).get("message", "")
                    print("mega-iter", rr.status_code, msg)
                    results.append({"mega_iter": {"status": rr.status_code, "msg": msg}})
                    if rr.status_code in (200, 201):
                        winning = body
                        results.append({"success": jb})
                        break
                    import re
                    m = re.search(r"(.+?) is (?:a )?required", str(msg), re.I)
                    if not m:
                        break
                    label = m.group(1).strip()
                    # camelCase guess
                    parts = label.split()
                    key = parts[0].lower() + "".join(x.capitalize() for x in parts[1:]) if len(parts) > 1 else (label[:1].lower() + label[1:])
                    if key in body:
                        break
                    body = {**body, key: buy["symbol"] if "name" in key.lower() or "sec" in key.lower() else (px if "price" in key.lower() else 1)}
            else:
                # Try raw string body with price as unquoted? invalid JSON
                # Try price inside Money as string amount
                for extra in [
                    {"price": {"currency": "NGN", "amount": f"{px:.2f}"}},
                    {"price": {"currency": "NGN", "amount": px, "scale": 2}},
                    {"price": {"currency": {"code": "NGN"}, "amount": px}},
                    {"Price": {"Currency": "NGN", "Amount": px}},
                    {"price": {"CurrencyCode": "NGN", "Value": px}},
                ]:
                    body = {**base, **extra}
                    rr = await client.post(ap.CREATE_ORDER, headers=secure, json=body)
                    msg = (rr.json().get("error") or {}).get("message")
                    print("money", list(extra)[0], type(list(extra.values())[0]).__name__, rr.status_code, msg)
                    results.append({"money": extra, "status": rr.status_code, "msg": msg})
                    if rr.status_code in (200, 201) or (msg and "Price is required" not in msg):
                        print("money progress", msg)
                        if rr.status_code in (200, 201):
                            winning = body
                            break

        if winning:
            print("WIN", {k: winning[k] for k in winning if k != "portfolioId"})
            results.append({"winning": winning})

    OUT.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT, "hits", len([r for r in results if "hit" in r]))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
