#!/usr/bin/env python3
"""Hit Java investing /orders directly + try CHD create with dual number encodings."""

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

OUT = ROOT / "logs" / "investnaija_order_probe10.json"


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
            k: (v[:12] + "...[REDACTED]" if isinstance(v, str) and any(x in k.lower() for x in ("token", "signature", "password")) else redact(v))
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
    log: list[Any] = []

    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        login = (await client.post(ap.LOGIN, json={"email": email, "password": encrypt_password(password), "rememberMe": True})).json()
        token, uuid_token = login["token"], login["uuid_token"]
        auth = {**headers, "Authorization": token, "x-uuid-token": uuid_token}
        # also try Bearer
        auth_bearer = {**headers, "Authorization": f"Bearer {token}", "x-uuid-token": uuid_token}
        p = (await client.get(ap.TRADE_PORTFOLIOS, headers=auth)).json()["data"][0]
        secure = {**auth, "x-secure-token": p["signature"]}
        secure_bearer = {**auth_bearer, "x-secure-token": p["signature"]}
        day_term = next(t for t in (await client.get(ap.ORDER_TERMS, headers=auth)).json()["data"]["result"] if "DAY" in t["label"].upper())
        buy = next(s for s in (await client.get(ap.SECURITIES, headers=auth)).json()["data"] if s.get("symbol") == "JAIZBANK")
        hold = p["portfolioHoldings"][0]
        px = round(float(buy["price"]), 2)

        core = {
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
            "price": px,
            "limitPrice": px,
        }

        # Direct investing endpoints
        urls = [
            "https://invest-chd.zanibal.com/investing/api/v1/orders",
            "https://invest-chd.zanibal.com/investing/api/v1/trade-orders",
            "https://invest-chd.zanibal.com/investing/api/v1/orders/create",
            "https://invest-chd.zanibal.com/investing/api/v1/orders/equity",
            "https://invest-chd.zanibal.com/api/investing/v1/orders",
            ap.CREATE_ORDER,
        ]

        money_bodies = [
            ("nums", core),
            ("lp_money", {**core, "limitPrice": {"currency": "NGN", "amount": px}}),
            ("price_money", {**core, "price": {"currency": "NGN", "amount": px}, "limitPrice": {"currency": "NGN", "amount": px}}),
            ("javaish", {
                "portfolioId": p["id"],
                "securityId": buy["id"],
                "securityName": buy["secId"],
                "quantityRequested": 1,
                "orderType": "LIMIT",
                "priceType": "LIMIT",
                "orderTermId": day_term["id"],
                "orderTermName": day_term["name"],
                "side": "BUY",
                "limitPrice": {"currency": "NGN", "amount": px},
                "marketPrice": {"currency": "NGN", "amount": px},
            }),
            # BigDecimal as string inside money — sometimes used
            ("money_str_amt", {**core, "limitPrice": {"currency": "NGN", "amount": f"{px:.2f}"}, "price": {"currency": "NGN", "amount": f"{px:.2f}"}}),
        ]

        winning = None
        for url in urls:
            for hname, hdrs in (("jwt", secure), ("bearer", secure_bearer)):
                for bname, body in money_bodies:
                    rr = await client.post(url, headers=hdrs, json=body)
                    try:
                        jb = rr.json()
                    except Exception:
                        jb = {"raw": rr.text[:300]}
                    msg = str(jb)[:240]
                    if rr.status_code not in (404, 405, 401, 403) or "order" in msg.lower():
                        if rr.status_code not in (404, 405):
                            print(url.split(".com")[-1][:40], hname, bname, rr.status_code, msg[:160])
                            log.append({"url": url, "auth": hname, "body": bname, "status": rr.status_code, "msg": msg})
                    if rr.status_code in (200, 201):
                        winning = {"url": url, "body": body, "resp": jb, "auth": hname}
                        break
                if winning:
                    break
            if winning:
                break

        # Try CHD create with limitPrice as number BUT Content-Type that might change parsing
        # Also try sending limitPrice twice via array? no.
        # Try Decimal string that CHD might coerce: actually CHD rejects strings.
        # Try limitPrice as integer kobo while price is naira float
        for bname, body in [
            ("kobo", {**core, "limitPrice": int(px * 100), "price": int(px * 100), "marketPrice": int(px * 100)}),
            ("float_only_lp", {**{k: v for k, v in core.items() if k not in ("price",)}, }),
            ("calc_merge", {**core, **{
                "fees": 6.615395,
                "consideration": px,
                "totalConsideration": px,
                "amount": 0.99,
            }}),
        ]:
            rr = await client.post(ap.CREATE_ORDER, headers=secure, json=body)
            jb = rr.json()
            msg = str(jb)[:220]
            print("chd", bname, rr.status_code, msg[:160])
            log.append({"chd": bname, "status": rr.status_code, "msg": msg})
            if rr.status_code in (200, 201):
                winning = {"url": ap.CREATE_ORDER, "body": body, "resp": jb, "auth": "jwt"}
                break

        # OPTIONS / GET on create to see Allow
        rr = await client.options(ap.CREATE_ORDER, headers=secure)
        print("OPTIONS create", rr.status_code, dict(rr.headers).get("allow"), rr.text[:100])

        if winning:
            print("WINNING", winning["url"], winning.get("auth"))
            log.append({"winning": {"url": winning["url"], "auth": winning["auth"], "body": {k: v for k, v in winning["body"].items() if k != "portfolioId"}, "resp": redact(winning["resp"])}})
            if live:
                sell = dict(winning["body"])
                sell["side"] = "SELL"
                sell["securityId"] = hold["securityId"]
                sell["securityName"] = hold.get("symbol")
                sell["quantityRequested"] = 1
                spx = round(float(hold.get("marketPrice") or 1), 2)
                for k, v in list(sell.items()):
                    if isinstance(v, (int, float)) and "price" in k.lower():
                        sell[k] = spx
                    elif isinstance(v, dict) and "amount" in v:
                        sell[k] = {**v, "amount": spx}
                rr = await client.post(winning["url"], headers=secure if winning["auth"] == "jwt" else secure_bearer, json=sell)
                print("SELL", rr.status_code, str(rr.json())[:300])
                log.append({"SELL": {"status": rr.status_code, "resp": redact(rr.json())}})

    OUT.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
