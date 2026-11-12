#!/usr/bin/env python3
"""Dump exact request wire bytes and try label-key + money shape variants."""

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

OUT = ROOT / "logs" / "investnaija_order_probe6.json"


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
        pid, sig = p["id"], p["signature"]
        secure = {**auth, "x-secure-token": sig}
        day_term = next(t for t in (await client.get(ap.ORDER_TERMS, headers=auth)).json()["data"]["result"] if "DAY" in t["label"].upper())
        buy = next(s for s in (await client.get(ap.SECURITIES, headers=auth)).json()["data"] if s.get("symbol") == "JAIZBANK")
        px = float(buy["price"])

        variants = []
        # label keys exactly as error messages
        variants.append(("labels", {
            "portfolioId": pid,
            "securityId": buy["id"],
            "Quantity Requested": 1,
            "Order Type": "LIMIT",
            "Price Type": "LIMIT",
            "Price": px,
            "orderTermId": day_term["id"],
            "side": "BUY",
        }))
        # both camel and labels
        variants.append(("both", {
            "portfolioId": pid,
            "securityId": buy["id"],
            "quantityRequested": 1,
            "Quantity Requested": 1,
            "orderType": "LIMIT",
            "Order Type": "LIMIT",
            "priceType": "LIMIT",
            "Price Type": "LIMIT",
            "price": px,
            "Price": px,
            "limitPrice": px,
            "orderTermId": day_term["id"],
            "side": "BUY",
        }))
        # try amount as top-level (consideration)
        variants.append(("amount_top", {
            "portfolioId": pid,
            "securityId": buy["id"],
            "quantityRequested": 1,
            "orderType": "LIMIT",
            "priceType": "LIMIT",
            "amount": px,
            "orderTermId": day_term["id"],
            "side": "BUY",
        }))
        # try all money-like shapes Zanibal uses
        for shape_name, price_val in [
            ("cur_amt", {"currency": "NGN", "amount": px}),
            ("ccy_amt", {"ccy": "NGN", "amount": px}),
            ("currencyCode_amt", {"currencyCode": "NGN", "amount": px}),
            ("currency_value", {"currency": "NGN", "value": px}),
            ("code_amount", {"code": "NGN", "amount": px}),
            ("iso_amount", {"isoCode": "NGN", "amount": px}),
            ("nested_number", {"number": px}),
            ("nested_raw", {"raw": px}),
            ("tuple_like", [ "NGN", px ]),
        ]:
            variants.append((f"price_{shape_name}", {
                "portfolioId": pid,
                "securityId": buy["id"],
                "quantityRequested": 1,
                "orderType": "LIMIT",
                "priceType": "LIMIT",
                "price": price_val,
                "orderTermId": day_term["id"],
                "side": "BUY",
            }))
            variants.append((f"limitPrice_{shape_name}", {
                "portfolioId": pid,
                "securityId": buy["id"],
                "quantityRequested": 1,
                "orderType": "LIMIT",
                "priceType": "LIMIT",
                "limitPrice": price_val,
                "orderTermId": day_term["id"],
                "side": "BUY",
            }))

        # Maybe they want price on query string
        qbody = {
            "portfolioId": pid,
            "securityId": buy["id"],
            "quantityRequested": 1,
            "orderType": "LIMIT",
            "priceType": "LIMIT",
            "orderTermId": day_term["id"],
            "side": "BUY",
        }
        variants.append(("query_price", None))  # special-cased below

        for name, body in variants:
            if name == "query_price":
                rr = await client.post(
                    ap.CALCULATE_PRICE + f"?price={px}&limitPrice={px}",
                    headers=secure,
                    json=qbody,
                )
                req_preview = {"query": f"price={px}", "body": qbody}
            else:
                # send raw content to verify serialization
                content = json.dumps(body, separators=(",", ":"))
                rr = await client.post(ap.CALCULATE_PRICE, headers=secure, content=content)
                req_preview = json.loads(content)
            try:
                jb = rr.json()
            except Exception:
                jb = {"raw": rr.text[:300]}
            msg = str(jb)[:240]
            print(name, rr.status_code, msg)
            results.append({"name": name, "status": rr.status_code, "msg": msg, "req": req_preview})
            if rr.status_code == 200:
                print("WINNING", name)
                break

        # Also try CREATE directly — maybe calculate validates differently
        create_body = {
            "portfolioId": pid,
            "securityId": buy["id"],
            "quantityRequested": 1,
            "orderType": "LIMIT",
            "priceType": "LIMIT",
            "price": px,
            "limitPrice": px,
            "orderTermId": day_term["id"],
            "side": "BUY",
            "symbol": buy["symbol"],
        }
        rr = await client.post(ap.CREATE_ORDER, headers=secure, json=create_body)
        try:
            jb = rr.json()
        except Exception:
            jb = {"raw": rr.text[:300]}
        print("CREATE direct", rr.status_code, str(jb)[:240])
        results.append({"name": "CREATE_direct", "status": rr.status_code, "msg": str(jb)[:240], "req": create_body})

    OUT.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
