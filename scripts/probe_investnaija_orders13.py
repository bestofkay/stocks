#!/usr/bin/env python3
"""Try MARKET orders and alternate Java DTO field names for create."""

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

OUT = ROOT / "logs" / "investnaija_order_probe13.json"


def encrypt_password(pw: str) -> str:
    der = base64.b64decode(ap.PASSWORD_PUBLIC_KEY_B64)
    key = serialization.load_der_public_key(der)
    ct = key.encrypt(
        pw.encode("utf-8"),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return base64.b64encode(ct).decode("ascii")


def classify(jb: Any) -> str:
    if not isinstance(jb, dict):
        return str(jb)[:200]
    err = jb.get("error")
    if isinstance(err, dict):
        m = str(err.get("message") or "")
        if "Failed to read request" in m:
            return "JAVA_FAIL"
        return m[:200]
    if jb.get("code") == 200 or jb.get("status") in ("success", 200) or jb.get("success") is True:
        return "SUCCESS:" + json.dumps(jb)[:180]
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
    log: list[Any] = []

    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        login = (await client.post(ap.LOGIN, json={"email": email, "password": encrypt_password(password), "rememberMe": True})).json()
        auth = {**headers, "Authorization": login["token"], "x-uuid-token": login["uuid_token"]}
        p = (await client.get(ap.TRADE_PORTFOLIOS, headers=auth)).json()["data"][0]
        secure = {**auth, "x-secure-token": p["signature"]}
        day_term = next(t for t in (await client.get(ap.ORDER_TERMS, headers=auth)).json()["data"]["result"] if "DAY" in t["label"].upper())
        buy = next(s for s in (await client.get(ap.SECURITIES, headers=auth)).json()["data"] if s.get("symbol") == "JAIZBANK")
        hold = p["portfolioHoldings"][0]
        px = round(float(buy["price"]), 2)

        async def post(body: dict) -> tuple[int, str, Any]:
            rr = await client.post(ap.CREATE_ORDER, headers=secure, json=body)
            jb = rr.json()
            return rr.status_code, classify(jb), jb

        # Confirm min CHD->Java body without `price`
        min_body = {
            "portfolioId": p["id"],
            "securityId": buy["id"],
            "securityName": buy["secId"],
            "quantityRequested": 1,
            "orderType": "LIMIT",
            "priceType": "LIMIT",
            "orderTermId": day_term["id"],
            "orderTermName": day_term["name"],
            "side": "BUY",
            "marketPrice": px,
            "limitPrice": px,
        }
        st, msg, _ = await post(min_body)
        print("min", st, msg)
        log.append({"min": msg})

        # MARKET variants
        for ot, pt in [("MARKET", "MARKET"), ("MARKET", "LIMIT"), ("LIMIT", "MARKET")]:
            body = {
                **{k: v for k, v in min_body.items() if k not in ("limitPrice", "marketPrice")},
                "orderType": ot,
                "priceType": pt,
            }
            # try with and without prices
            for label, extra in [
                ("none", {}),
                ("mp", {"marketPrice": px}),
                ("lp", {"limitPrice": px}),
                ("both", {"marketPrice": px, "limitPrice": px}),
                ("zero", {"marketPrice": 0, "limitPrice": 0}),
            ]:
                b = {**body, **extra}
                st, msg, jb = await post(b)
                print(f"{ot}/{pt}/{label}", st, msg)
                log.append({"trial": f"{ot}/{pt}/{label}", "status": st, "msg": msg})
                if msg.startswith("SUCCESS") or st in (200, 201):
                    log.append({"winning": b, "resp": jb})
                    print("WIN", b)
                    if live:
                        sell = dict(b)
                        sell.update({"side": "SELL", "securityId": hold["securityId"], "securityName": hold.get("symbol"), "quantityRequested": 1})
                        spx = round(float(hold.get("marketPrice") or 1), 2)
                        for k in ("marketPrice", "limitPrice", "price"):
                            if k in sell:
                                sell[k] = spx
                        st2, msg2, jb2 = await post(sell)
                        print("SELL", st2, msg2)
                        log.append({"SELL": {"status": st2, "msg": msg2, "resp": jb2}})
                    OUT.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
                    return 0

        # Try renaming fields to Java-typical names while keeping CHD copies
        # e.g. send both limitPrice (number for CHD) and a Java Money under a name CHD ignores
        java_aliases = [
            ("limitPriceAmt", px),
            ("limitPriceAmount", px),
            ("limit_price", px),
            ("orderLimitPrice", px),
            ("lmtPrice", px),
            ("priceLimit", px),
            ("limitPriceValue", px),
            ("limitPriceDto", {"currency": "NGN", "amount": px}),
            ("limitPriceModel", {"currency": "NGN", "amount": px}),
            ("priceModel", {"currency": "NGN", "amount": px}),
            ("money", {"currency": "NGN", "amount": px}),
            ("unitPrice", px),
            ("bidPrice", px),
            ("offerPrice", px),
            ("stopPrice", 0),
            ("pegPrice", px),
            ("discretionOffset", 0),
            ("cashOrderQty", 0),
            ("allOrNone", False),
            ("anonymous", False),
            ("timeInForce", day_term.get("fixTimeInForce")),
            ("tif", "DAY"),
            ("expiryDate", None),
            ("goodTillDate", None),
            ("currency", "NGN"),
            ("board", "EQTY"),
            ("marketCode", "NGX"),
            ("securityExchange", "NGX"),
            ("accountNo", p.get("accountNo")),
            ("tradingAccountNo", p.get("tradingAccountNo")),
            ("clientOrderId", "test-1"),
            ("clOrdId", "test-1"),
            ("notes", "aggregator"),
        ]
        # Adding fields one at a time — if JAVA_FAIL becomes something else, we found a lead
        # Actually adding shouldn't fix FAIL_ON_UNKNOWN — removing/renaming would.
        # So try REPLACING limitPrice key with java name while ALSO having marketPrice
        # CHD might accept marketPrice as Price for MARKET but for LIMIT needs limitPrice...

        # What if Jackson fails because quantityRequested should be Long and 1 is fine,
        # but orderTermId integer overflows? Unlikely for 3680441.
        # What if securityId UUID format with extra chars fails?
        # Try without hyphens?

        # Capture Spring error with different Accept
        for accept in ("application/json", "application/problem+json", "*/*"):
            rr = await client.post(ap.CREATE_ORDER, headers={**secure, "Accept": accept}, json=min_body)
            print("accept", accept, rr.status_code, rr.text[:300])
            log.append({"accept": accept, "text": rr.text[:300]})

        # Try calculate first then create with exact calc response fields merged carefully
        calc = (await client.post(ap.CALCULATE_PRICE, headers=secure, json={**min_body, "price": px})).json()
        print("calc ok", calc.get("code"), calc.get("data"))
        merged = {**min_body, **(calc.get("data") or {})}
        # feesBreakdown might break Java!
        st, msg, jb = await post(merged)
        print("merged_all_calc", st, msg)
        log.append({"merged_all_calc": msg})
        # without feesBreakdown
        merged2 = {**min_body, "fees": calc["data"]["fees"], "consideration": calc["data"]["consideration"], "totalConsideration": calc["data"]["totalConsideration"], "amount": calc["data"]["amount"]}
        st, msg, jb = await post(merged2)
        print("merged_scalars", st, msg)
        log.append({"merged_scalars": msg})

    OUT.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
