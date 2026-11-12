#!/usr/bin/env python3
"""Create-order with Money-shaped limitPrice for the Java investing service."""

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

OUT = ROOT / "logs" / "investnaija_order_probe9.json"


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
        money = {"currency": "NGN", "amount": px}
        money_alts = [
            {"currency": "NGN", "amount": px},
            {"amount": px, "currency": "NGN"},
            {"currencyCode": "NGN", "amount": px},
            {"ccy": "NGN", "amount": px},
            {"currency": {"code": "NGN"}, "amount": px},
            {"currency": "NGN", "value": px},
            {"code": "NGN", "amount": px},
        ]

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

        variants: list[tuple[str, dict[str, Any]]] = []
        for i, m in enumerate(money_alts):
            variants.append((f"limitPrice_money_{i}", {**base, "limitPrice": m}))
            variants.append((f"price_money_{i}", {**base, "price": m}))
            variants.append((f"both_money_{i}", {**base, "price": m, "limitPrice": m}))
            variants.append((f"all_three_{i}", {**base, "price": m, "limitPrice": m, "marketPrice": m if isinstance(m, dict) else px}))
            variants.append((f"lp_money_price_num_{i}", {**base, "limitPrice": m, "price": px}))
            variants.append((f"price_money_lp_num_{i}", {**base, "price": m, "limitPrice": px}))

        # Also try amount field as Money
        variants.append(("amount_money", {**base, "amount": money, "price": px}))
        variants.append(("orderPrice_money", {**base, "orderPrice": money}))
        variants.append(("requestedPrice_money", {**base, "requestedPrice": money}))

        winning = None
        for name, body in variants:
            rr = await client.post(ap.CREATE_ORDER, headers=secure, json=body)
            try:
                jb = rr.json()
            except Exception:
                jb = {"raw": rr.text[:300]}
            err = jb.get("error") if isinstance(jb, dict) else None
            msg = err.get("message") if isinstance(err, dict) else str(jb)[:220]
            # unwrap stringified json errors
            if isinstance(msg, str) and msg.startswith("{") and "Failed to read" in msg:
                short = "Failed to read request"
            else:
                short = str(msg)[:180]
            interesting = rr.status_code in (200, 201) or (
                rr.status_code == 400
                and "Price is required" not in short
                and "Failed to read" not in short
            )
            if interesting or name.endswith("_0"):
                print(name, rr.status_code, short)
            log.append({"name": name, "status": rr.status_code, "msg": short})
            if rr.status_code in (200, 201):
                winning = body
                log.append({"success": redact(jb), "body": {k: v for k, v in body.items() if k != "portfolioId"}})
                break
            if interesting:
                print("  PROGRESS body keys", sorted(body.keys()))
                # continue filling if required
                import re
                cur = dict(body)
                for _ in range(15):
                    rr2 = await client.post(ap.CREATE_ORDER, headers=secure, json=cur)
                    jb2 = rr2.json()
                    err2 = jb2.get("error") if isinstance(jb2, dict) else None
                    msg2 = err2.get("message") if isinstance(err2, dict) else str(jb2)[:220]
                    print("  ", rr2.status_code, str(msg2)[:180])
                    log.append({"iter": str(msg2)[:220], "status": rr2.status_code})
                    if rr2.status_code in (200, 201):
                        winning = cur
                        log.append({"success": redact(jb2)})
                        break
                    m = re.search(r"(.+?) is (?:a )?required", str(msg2), re.I)
                    if not m:
                        break
                    label = m.group(1).strip()
                    parts = label.split()
                    key = parts[0].lower() + "".join(x.capitalize() for x in parts[1:]) if len(parts) > 1 else label[:1].lower() + label[1:]
                    aliases = {
                        "Security Name": "securityName",
                        "Order Term Name": "orderTermName",
                        "Board": "board",
                        "Board Name": "board",
                        "Market": "marketCode",
                        "Currency": "currency",
                        "Account": "accountNo",
                        "Symbol": "symbol",
                        "Time In Force": "timeInForce",
                        "Order Side": "side",
                    }
                    key = aliases.get(label, key)
                    if key in cur:
                        break
                    val: Any
                    if "price" in key.lower():
                        val = money
                    elif "side" in key.lower():
                        val = "BUY"
                    elif "board" in key.lower():
                        val = "EQTY"
                    elif "market" in key.lower():
                        val = "NGX"
                    elif "currency" in key.lower():
                        val = "NGN"
                    elif "account" in key.lower():
                        val = p.get("accountNo")
                    elif "term" in key.lower():
                        val = day_term["name"]
                    elif "symbol" in key.lower() or "name" in key.lower():
                        val = buy["secId"]
                    else:
                        val = 1
                    cur[key] = val
                if winning:
                    break

        if winning:
            print("WINNING", json.dumps({k: v for k, v in winning.items() if k != "portfolioId"}))
            if live or True:
                # Always attempt sell if buy succeeded when LIVE_ORDER=1; if we got success already in loop, buy is done
                if live:
                    sell = dict(winning)
                    sell["side"] = "SELL"
                    sell["securityId"] = hold["securityId"]
                    sell["securityName"] = hold.get("symbol")
                    sell["quantityRequested"] = 1
                    spx = round(float(hold.get("marketPrice") or 1), 2)
                    smoney = {"currency": "NGN", "amount": spx}
                    for k, v in list(sell.items()):
                        if k == "marketPrice" and isinstance(v, (int, float)):
                            sell[k] = spx
                        elif isinstance(v, dict) and "amount" in v:
                            sell[k] = smoney
                        elif k in {"price", "limitPrice", "orderPrice"} and isinstance(v, (int, float)):
                            sell[k] = spx
                    rr = await client.post(ap.CREATE_ORDER, headers=secure, json=sell)
                    print("SELL", rr.status_code, str(rr.json())[:300])
                    log.append({"SELL": {"status": rr.status_code, "resp": redact(rr.json())}})
        else:
            print("still no win; sample msgs", [r for r in log if r.get("status") not in (None,) and "Price is required" not in str(r.get("msg")) and "Failed to read" not in str(r.get("msg"))][:10])

    OUT.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
