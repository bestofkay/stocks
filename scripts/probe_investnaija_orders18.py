#!/usr/bin/env python3
"""Last create attempts: mixed price types + detail.id as portfolioId + list sig."""

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


def encrypt_password(pw: str) -> str:
    der = base64.b64decode(ap.PASSWORD_PUBLIC_KEY_B64)
    key = serialization.load_der_public_key(der)
    ct = key.encrypt(
        pw.encode("utf-8"),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return base64.b64encode(ct).decode("ascii")


def classify(jb) -> str:
    err = (jb or {}).get("error") if isinstance(jb, dict) else None
    if isinstance(err, dict):
        m = str(err.get("message") or "")
        if "Failed to read" in m:
            return "JAVA_FAIL"
        return m[:200]
    if isinstance(jb, dict) and (jb.get("code") == 200 or jb.get("status") == "success"):
        return "SUCCESS:" + json.dumps(jb)[:200]
    return str(jb)[:200]


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
    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        login = (await client.post(ap.LOGIN, json={"email": email, "password": encrypt_password(password), "rememberMe": True})).json()
        auth = {**headers, "Authorization": login["token"], "x-uuid-token": login["uuid_token"]}
        p = (await client.get(ap.TRADE_PORTFOLIOS, headers=auth)).json()["data"][0]
        secure = {**auth, "x-secure-token": p["signature"]}
        detail = (await client.get(ap.trade_portfolio(p["id"]), headers=secure)).json()["data"]
        day_term = next(t for t in (await client.get(ap.ORDER_TERMS, headers=auth)).json()["data"]["result"] if "DAY" in t["label"].upper())
        buy = next(s for s in (await client.get(ap.SECURITIES, headers=auth)).json()["data"] if s.get("symbol") == "JAIZBANK")
        hold = p["portfolioHoldings"][0]
        px = round(float(buy["price"]), 2)
        money = {"currency": "NGN", "amount": px}

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

        mixes = [
            ("lp_num_mp_money", {**base, "limitPrice": px, "marketPrice": money}),
            ("lp_money_mp_num", {**base, "limitPrice": money, "marketPrice": px}),
            ("lp_num_price_money", {**base, "limitPrice": px, "marketPrice": px, "price": money}),
            ("lp_num_mp_num_price_money", {**base, "limitPrice": px, "marketPrice": px, "price": money}),
            ("ngn_string_money", {**base, "limitPrice": px, "marketPrice": px, "price": f"NGN {px:.2f}"}),
            # use detail.id with detail.signature (matched pair) — already JAVA_FAIL
            # try list portfolioId but send securityId as UUID without other fields that might confuse
            ("minimal_matched", {
                "portfolioId": p["id"],
                "securityName": buy["secId"],
                "quantityRequested": 1,
                "orderType": "LIMIT",
                "priceType": "LIMIT",
                "orderTermName": day_term["name"],
                "side": "BUY",
                "limitPrice": px,
                "marketPrice": px,
            }),
            # securityId as UUID is required by java maybe - include
            ("with_sec_uuid", {
                "portfolioId": p["id"],
                "securityId": buy["id"],
                "securityName": buy["secId"],
                "quantityRequested": 1,
                "orderType": "LIMIT",
                "priceType": "LIMIT",
                "orderTermName": day_term["name"],
                "side": "BUY",
                "limitPrice": px,
                "marketPrice": px,
            }),
            # Maybe orderTermId as object breaks; we omit it (worked for JAVA reach before)
        ]

        for name, body in mixes:
            rr = await client.post(ap.CREATE_ORDER, headers=secure, json=body)
            msg = classify(rr.json())
            print(name, rr.status_code, msg)
            if msg.startswith("SUCCESS") or rr.status_code in (200, 201):
                print("WIN", body)
                if live:
                    sell = dict(body)
                    sell["side"] = "SELL"
                    sell["securityId"] = hold["securityId"]
                    sell["securityName"] = hold.get("symbol")
                    spx = round(float(hold.get("marketPrice") or 1), 2)
                    for k in ("limitPrice", "marketPrice", "price"):
                        if k in sell and isinstance(sell[k], (int, float)):
                            sell[k] = spx
                        elif k in sell and isinstance(sell[k], dict):
                            sell[k] = {"currency": "NGN", "amount": spx}
                    rr2 = await client.post(ap.CREATE_ORDER, headers=secure, json=sell)
                    print("SELL", rr2.status_code, classify(rr2.json()))
                return 0

        # Dump exact wire request that Java receives by checking content-length quirks
        content = json.dumps({**base, "limitPrice": px, "marketPrice": px}, separators=(",", ":"))
        print("wire", content[:300], "len", len(content))
        rr = await client.post(ap.CREATE_ORDER, headers=secure, content=content.encode())
        print("wire post", rr.status_code, classify(rr.json()), rr.text[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
