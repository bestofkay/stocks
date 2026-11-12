#!/usr/bin/env python3
"""Try nested body shapes / alternate field layouts for calculate-price."""

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

OUT = ROOT / "logs" / "investnaija_order_probe5.json"


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

        order_core = {
            "portfolioId": pid,
            "securityId": buy["id"],
            "secId": buy.get("secId") or buy.get("symbol"),
            "symbol": buy["symbol"],
            "quantityRequested": 1,
            "orderType": "LIMIT",
            "priceType": "LIMIT",
            "price": px,
            "limitPrice": px,
            "orderTermId": day_term["id"],
            "orderTerm": day_term["name"],
            "side": "BUY",
            "orderSide": "BUY",
            "currency": "NGN",
            "marketCode": "NGX",
            "board": "EQTY",
        }

        bodies: list[tuple[str, Any]] = [
            ("flat", order_core),
            ("wrapped_data", {"data": order_core}),
            ("wrapped_order", {"order": order_core}),
            ("wrapped_payload", {"payload": order_core}),
            ("wrapped_tradeOrder", {"tradeOrder": order_core}),
            ("wrapped_request", {"request": order_core}),
            # Zanibal-style often uses these exact OMS names
            ("oms1", {
                "portfolioId": pid,
                "securityId": buy["id"],
                "quantityRequested": 1,
                "orderType": "LIMIT",
                "priceType": "LIMIT",
                "orderPrice": {"currency": "NGN", "amount": px},
                "orderTermId": day_term["id"],
                "side": "BUY",
            }),
            ("oms2", {
                "portfolioId": pid,
                "securityId": buy["id"],
                "quantityRequested": 1,
                "orderType": "LIMIT",
                "priceType": "LIMIT",
                "price": {"amount": px, "currency": "NGN"},
                "orderTermId": day_term["id"],
                "side": "BUY",
            }),
            ("oms3", {
                "portfolioId": pid,
                "securityId": buy["id"],
                "quantityRequested": {"amount": 1},
                "orderType": "LIMIT",
                "priceType": "LIMIT",
                "price": {"amount": px, "currency": "NGN"},
                "orderTermId": day_term["id"],
                "side": "BUY",
            }),
            ("oms4_price_only_amount", {
                "portfolioId": pid,
                "securityId": buy["id"],
                "quantityRequested": 1,
                "orderType": "LIMIT",
                "priceType": "LIMIT",
                "price": {"amount": px},
                "orderTermId": day_term["id"],
                "side": "BUY",
            }),
            ("oms5_price_value", {
                "portfolioId": pid,
                "securityId": buy["id"],
                "quantityRequested": 1,
                "orderType": "LIMIT",
                "priceType": "LIMIT",
                "price": {"value": px, "currency": "NGN"},
                "orderTermId": day_term["id"],
                "side": "BUY",
            }),
            ("oms6_nested_prices", {
                "portfolioId": pid,
                "securityId": buy["id"],
                "quantityRequested": 1,
                "orderType": "LIMIT",
                "priceType": "LIMIT",
                "prices": {"limit": px},
                "orderTermId": day_term["id"],
                "side": "BUY",
            }),
            ("oms7_limitPx", {
                "portfolioId": pid,
                "securityId": buy["id"],
                "quantityRequested": 1,
                "orderType": "LIMIT",
                "priceType": "LIMIT",
                "limitPx": px,
                "orderTermId": day_term["id"],
                "side": "BUY",
            }),
            ("oms8_orderPx", {
                "portfolioId": pid,
                "securityId": buy["id"],
                "quantityRequested": 1,
                "orderType": "LIMIT",
                "priceType": "LIMIT",
                "orderPx": px,
                "orderTermId": day_term["id"],
                "side": "BUY",
            }),
            ("oms9_fix_price", {
                "portfolioId": pid,
                "securityId": buy["id"],
                "quantityRequested": 1,
                "orderType": "LIMIT",
                "priceType": "LIMIT",
                "fixPrice": px,
                "orderTermId": day_term["id"],
                "side": "BUY",
            }),
            ("oms10_Price_capital", {
                "portfolioId": pid,
                "securityId": buy["id"],
                "quantityRequested": 1,
                "orderType": "LIMIT",
                "priceType": "LIMIT",
                "Price": {"currency": "NGN", "amount": px},
                "orderTermId": day_term["id"],
                "side": "BUY",
            }),
            # Maybe quantityRequested itself is money? Unlikely.
            # Try market order without price? maybe still needs priceType MARKET and price 0
            ("mkt0", {
                "portfolioId": pid,
                "securityId": buy["id"],
                "quantityRequested": 1,
                "orderType": "MARKET",
                "priceType": "MARKET",
                "price": 0,
                "orderTermId": day_term["id"],
                "side": "BUY",
            }),
            ("mkt1", {
                "portfolioId": pid,
                "securityId": buy["id"],
                "quantityRequested": 1,
                "orderType": "MARKET",
                "priceType": "MARKET",
                "price": {"currency": "NGN", "amount": 0},
                "orderTermId": day_term["id"],
                "side": "BUY",
            }),
        ]

        for name, body in bodies:
            rr = await client.post(ap.CALCULATE_PRICE, headers=secure, json=body)
            try:
                jb = rr.json()
            except Exception:
                jb = {"raw": rr.text[:300]}
            msg = str(jb)[:220]
            print(name, rr.status_code, msg)
            results.append({"name": name, "status": rr.status_code, "msg": msg})
            if rr.status_code == 200:
                print("WIN", name, jb)
                results.append({"winning": body})
                break

        # Also try form-encoded
        form = {
            "portfolioId": pid,
            "securityId": buy["id"],
            "quantityRequested": "1",
            "orderType": "LIMIT",
            "priceType": "LIMIT",
            "price": str(px),
            "orderTermId": str(day_term["id"]),
            "side": "BUY",
        }
        rr = await client.post(ap.CALCULATE_PRICE, headers={**secure, "Content-Type": "application/x-www-form-urlencoded"}, data=form)
        try:
            jb = rr.json()
        except Exception:
            jb = {"raw": rr.text[:300]}
        print("form", rr.status_code, str(jb)[:200])
        results.append({"name": "form", "status": rr.status_code, "msg": str(jb)[:200]})

    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
