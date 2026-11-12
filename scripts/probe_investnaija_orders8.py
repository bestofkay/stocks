#!/usr/bin/env python3
"""Finalize InvestNaija order create using the working calculate-price body."""

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

OUT = ROOT / "logs" / "investnaija_order_probe8.json"


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
        auth = {**headers, "Authorization": login["token"], "x-uuid-token": login["uuid_token"]}
        p = (await client.get(ap.TRADE_PORTFOLIOS, headers=auth)).json()["data"][0]
        secure = {**auth, "x-secure-token": p["signature"]}
        day_term = next(t for t in (await client.get(ap.ORDER_TERMS, headers=auth)).json()["data"]["result"] if "DAY" in t["label"].upper())
        buy = next(s for s in (await client.get(ap.SECURITIES, headers=auth)).json()["data"] if s.get("symbol") == "JAIZBANK")
        hold = p["portfolioHoldings"][0]
        px = round(float(buy["price"]), 2)

        calc_body = {
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
        rr = await client.post(ap.CALCULATE_PRICE, headers=secure, json=calc_body)
        calc = rr.json()
        print("CALC", rr.status_code, json.dumps(calc)[:500])
        log.append({"calc": {"status": rr.status_code, "resp": calc, "req": {k: v for k, v in calc_body.items() if k != "portfolioId"}}})

        create_urls = [
            ("CREATE_ORDER", ap.CREATE_ORDER),
            ("trade-order/create2", f"{ap.API_BASE}/trades/trade-order/create-order"),
            ("trade-order/submit", f"{ap.API_BASE}/trades/trade-order/submit"),
            ("trade-order/place", f"{ap.API_BASE}/trades/trade-order/place"),
            ("orders", f"{ap.API_BASE}/trades/orders"),
            ("order/create", f"{ap.API_BASE}/trades/order/create"),
            ("investing/orders", f"{ap.API_BASE}/investing/orders"),
            ("investing/api/v1/orders", "https://invest-chd.zanibal.com/investing/api/v1/orders"),
            ("api/v1/orders", "https://invest-chd.zanibal.com/api/v1/orders"),
            ("trades/trade-orders", f"{ap.API_BASE}/trades/trade-orders"),
            ("trades/trade-order", f"{ap.API_BASE}/trades/trade-order"),
        ]

        # Body variants for create — avoid limitPrice if it breaks deserialization,
        # but calculate needed it. Try both.
        bodies = [
            ("full_calc", calc_body),
            ("no_limitPrice", {k: v for k, v in calc_body.items() if k != "limitPrice"}),
            ("no_price", {k: v for k, v in calc_body.items() if k != "price"}),
            ("mp_price_only", {k: v for k, v in calc_body.items() if k != "limitPrice"}),
            ("with_fees", {**calc_body, "fees": (calc.get("data") or {}).get("fees"), "feesBreakdown": (calc.get("data") or {}).get("feesBreakdown")}),
            ("with_total", {**{k: v for k, v in calc_body.items() if k != "limitPrice"}, "totalAmount": (calc.get("data") or {}).get("total"), "estimatedFees": (calc.get("data") or {}).get("fees")}),
            ("price_as_market_only", {
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
            }),
            # Maybe create wants nested calculated order
            ("nested_data", {"data": calc_body}),
            ("nested_order", {"order": calc_body}),
            ("calc_plus_order", {**(calc.get("data") or {}), **calc_body}),
        ]

        winning = None
        for bname, body in bodies:
            for uname, url in create_urls:
                rr = await client.post(url, headers=secure, json=body)
                try:
                    jb = rr.json()
                except Exception:
                    jb = {"raw": rr.text[:300]}
                msg = str(jb)[:220]
                interesting = rr.status_code not in (404, 405) and not (
                    rr.status_code == 400 and ("Price is required" in msg or "Failed to read request" in msg or "Order Type is required" in msg)
                )
                if interesting or (rr.status_code in (200, 201)) or (rr.status_code == 400 and "Price is required" not in msg and "Failed to read" not in msg and "Order Type" not in msg):
                    print(bname, uname, rr.status_code, msg)
                    log.append({"body": bname, "url": uname, "status": rr.status_code, "msg": msg, "resp": redact(jb)})
                if rr.status_code in (200, 201):
                    winning = {"body": body, "url": url, "uname": uname, "bname": bname, "resp": jb}
                    break
                # progress beyond known errors
                if rr.status_code == 400 and "Price is required" not in msg and "Failed to read request" not in msg and "Order Type is required" not in msg and "required" in msg.lower():
                    print("PROGRESS", bname, uname, msg)
                    # try to fill
                    cur = dict(body) if isinstance(body, dict) and "portfolioId" in body else dict(calc_body)
                    import re
                    for _ in range(12):
                        rr2 = await client.post(url, headers=secure, json=cur)
                        jb2 = rr2.json()
                        msg2 = str((jb2.get("error") or {}).get("message") or jb2)[:220]
                        print(" ", rr2.status_code, msg2)
                        log.append({"fill": msg2, "status": rr2.status_code})
                        if rr2.status_code in (200, 201):
                            winning = {"body": cur, "url": url, "uname": uname, "bname": bname, "resp": jb2}
                            break
                        m = re.search(r"(.+?) is (?:a )?required", msg2, re.I)
                        if not m:
                            break
                        label = m.group(1).strip()
                        parts = label.split()
                        key = parts[0].lower() + "".join(x.capitalize() for x in parts[1:]) if len(parts) > 1 else label[:1].lower() + label[1:]
                        aliases = {
                            "Security Name": "securityName",
                            "Order Term Name": "orderTermName",
                            "Board Name": "board",
                            "Board": "board",
                            "Market": "marketCode",
                            "Currency": "currency",
                            "Account": "accountNo",
                            "Symbol": "symbol",
                            "Time In Force": "timeInForce",
                        }
                        key = aliases.get(label, key)
                        val: Any = px if "price" in key.lower() else (
                            "BUY" if "side" in key.lower() else (
                                "EQTY" if "board" in key.lower() else (
                                    "NGX" if "market" in key.lower() or "exchange" in key.lower() else (
                                        "NGN" if "currency" in key.lower() else (
                                            day_term["name"] if "term" in key.lower() else (
                                                buy["secId"] if "name" in key.lower() or "sec" in key.lower() or "symbol" in key.lower() else (
                                                    p.get("accountNo") if "account" in key.lower() else 1
                                                )
                                            )
                                        )
                                    )
                                )
                            )
                        )
                        cur = {**cur, key: val}
                    if winning:
                        break
            if winning:
                break

        if winning:
            print("WINNING CREATE", winning["uname"], winning["bname"])
            log.append({"winning": {"url": winning["url"], "uname": winning["uname"], "bname": winning["bname"], "body": {k: v for k, v in winning["body"].items() if k != "portfolioId"}, "resp": redact(winning["resp"])}})
            if live:
                sell_body = dict(winning["body"])
                sell_body["side"] = "SELL"
                sell_body["securityId"] = hold["securityId"]
                sell_body["securityName"] = hold.get("symbol")
                sell_body["symbol"] = hold.get("symbol")
                sell_body["quantityRequested"] = 1
                spx = round(float(hold.get("marketPrice") or 1), 2)
                for k in ("marketPrice", "price", "limitPrice"):
                    if k in sell_body:
                        sell_body[k] = spx
                rr = await client.post(winning["url"], headers=secure, json=sell_body)
                print("SELL", rr.status_code, str(rr.json())[:300])
                log.append({"SELL": {"status": rr.status_code, "resp": redact(rr.json()), "req": {k: v for k, v in sell_body.items() if k != "portfolioId"}}})
        else:
            print("no winning create yet — dumping create responses for full_calc across non-404 urls")
            for uname, url in create_urls:
                rr = await client.post(url, headers=secure, json=calc_body)
                try:
                    jb = rr.json()
                except Exception:
                    jb = {"raw": rr.text[:200]}
                if rr.status_code != 404:
                    print("dump", uname, rr.status_code, str(jb)[:200])
                    log.append({"dump": uname, "status": rr.status_code, "resp": redact(jb)})

    OUT.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
