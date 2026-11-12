#!/usr/bin/env python3
"""Combine marketPrice with other fields; finish create-order discovery."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.connectors.investnaija import api_paths as ap  # noqa: E402

OUT = ROOT / "logs" / "investnaija_order_probe7.json"


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
    log: list[Any] = []

    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        login = (await client.post(ap.LOGIN, json={"email": email, "password": encrypt_password(password), "rememberMe": True})).json()
        auth = {**headers, "Authorization": login["token"], "x-uuid-token": login["uuid_token"]}
        p = (await client.get(ap.TRADE_PORTFOLIOS, headers=auth)).json()["data"][0]
        secure = {**auth, "x-secure-token": p["signature"]}
        day_term = next(t for t in (await client.get(ap.ORDER_TERMS, headers=auth)).json()["data"]["result"] if "DAY" in t["label"].upper())
        buy = next(s for s in (await client.get(ap.SECURITIES, headers=auth)).json()["data"] if s.get("symbol") == "JAIZBANK")
        hold = p["portfolioHoldings"][0]
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
            "marketPrice": px,
        }

        combos = [
            ("mp_only", {}),
            ("mp_price", {"price": px}),
            ("mp_limit", {"limitPrice": px}),
            ("mp_both", {"price": px, "limitPrice": px}),
            ("mp_orderPrice", {"orderPrice": px}),
            ("mp_all", {"price": px, "limitPrice": px, "orderPrice": px, "requestedPrice": px, "unitPrice": px}),
            ("mp_money", {"price": {"currency": "NGN", "amount": px}}),
            ("mp_int", {"marketPrice": int(px)}),
            ("mp_round2", {"marketPrice": round(px, 2), "price": round(px, 2), "limitPrice": round(px, 2)}),
            # maybe priceType values interact — try MARKET with marketPrice
            ("mkt_type", {"orderType": "MARKET", "priceType": "MARKET", "marketPrice": px, "price": 0}),
            ("mp_sec_fields", {
                "price": px, "limitPrice": px,
                "symbol": buy["symbol"], "secId": buy["secId"],
                "marketCode": "NGX", "board": "EQTY", "currency": "NGN",
                "orderSide": "BUY", "accountNo": p.get("accountNo"),
            }),
        ]

        winning = None
        body = dict(base)
        for name, extra in combos:
            body = {**base, **extra}
            # ensure marketPrice always number
            if "marketPrice" in extra:
                body["marketPrice"] = extra["marketPrice"]
            rr = await client.post(ap.CREATE_ORDER, headers=secure, json=body)
            jb = rr.json()
            msg = (jb.get("error") or {}).get("message", str(jb)[:200])
            print(name, rr.status_code, msg)
            log.append({"combo": name, "status": rr.status_code, "msg": msg})
            if rr.status_code in (200, 201):
                winning = body
                log.append({"success": redact(jb)})
                break
            if "Price is required" not in str(msg):
                print("  progressed with", name)
                # iterate remaining required fields
                for _ in range(20):
                    rr = await client.post(ap.CREATE_ORDER, headers=secure, json=body)
                    jb = rr.json()
                    msg = (jb.get("error") or {}).get("message", "")
                    print("  iter", rr.status_code, msg)
                    log.append({"iter": msg, "status": rr.status_code, "keys": sorted(body.keys())})
                    if rr.status_code in (200, 201):
                        winning = body
                        log.append({"success": redact(jb)})
                        break
                    m = re.search(r"(.+?) is (?:a )?required", str(msg), re.I)
                    if not m:
                        # non-required error — might be business rule; still useful
                        if rr.status_code == 400:
                            print("  business?", msg)
                        break
                    label = m.group(1).strip()
                    parts = label.split()
                    key = parts[0].lower() + "".join(x.capitalize() for x in parts[1:]) if len(parts) > 1 else label[:1].lower() + label[1:]
                    aliases = {
                        "Security Name": "securityName",
                        "Order Term Name": "orderTermName",
                        "Order Side": "side",
                        "Limit Price": "limitPrice",
                        "Price": "marketPrice",
                        "Board": "board",
                        "Market": "marketCode",
                        "Currency": "currency",
                        "Account": "accountNo",
                        "Symbol": "symbol",
                        "Time In Force": "timeInForce",
                        "Order Validity": "orderTermName",
                    }
                    key = aliases.get(label, key)
                    val: Any
                    if "term" in key.lower():
                        val = day_term["name"]
                    elif "side" in key.lower():
                        val = "BUY"
                    elif "price" in key.lower() or key == "marketPrice":
                        val = px
                    elif "board" in key.lower():
                        val = "EQTY"
                    elif "market" in key.lower():
                        val = "NGX"
                    elif "currency" in key.lower():
                        val = "NGN"
                    elif "account" in key.lower():
                        val = p.get("accountNo")
                    elif "symbol" in key.lower() or "name" in key.lower():
                        val = buy.get("secId") or buy.get("symbol")
                    elif "tif" in key.lower() or "time" in key.lower():
                        val = day_term.get("fixTimeInForce")
                    else:
                        val = buy.get("symbol")
                    if key in body and body[key] == val:
                        # try alternate
                        if "price" in key.lower():
                            body = {**body, "price": px, "limitPrice": px, "marketPrice": px}
                        else:
                            print("stuck", label, key)
                            break
                    else:
                        body = {**body, key: val}
                if winning:
                    break

        # Also retry calculate-price with marketPrice
        if not winning:
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
            print("calc", rr.status_code, str(rr.json())[:240])
            log.append({"calc": {"status": rr.status_code, "resp": redact(rr.json())}})

        if winning:
            print("WINNING", json.dumps({k: v for k, v in winning.items() if k != "portfolioId"}))
            if live:
                # buy already placed if success; place sell of 1 ACCESSCORP
                sell = dict(winning)
                sell["side"] = "SELL"
                sell["orderSide"] = "SELL"
                sell["securityId"] = hold["securityId"]
                sell["securityName"] = hold.get("symbol")
                sell["symbol"] = hold.get("symbol")
                sell["secId"] = hold.get("symbol")
                sell["quantityRequested"] = 1
                spx = float(hold.get("marketPrice") or 1)
                for k in ("marketPrice", "price", "limitPrice", "orderPrice"):
                    if k in sell:
                        sell[k] = spx
                rr = await client.post(ap.CREATE_ORDER, headers=secure, json=sell)
                print("SELL", rr.status_code, str(rr.json())[:300])
                log.append({"SELL": {"status": rr.status_code, "resp": redact(rr.json()), "req": sell}})

    OUT.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
