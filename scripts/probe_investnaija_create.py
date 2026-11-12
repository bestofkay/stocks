#!/usr/bin/env python3
"""Iteratively satisfy InvestNaija trade-order/create required fields."""

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

OUT = ROOT / "logs" / "investnaija_create_probe.json"


def encrypt_password(pw: str) -> str:
    der = base64.b64decode(ap.PASSWORD_PUBLIC_KEY_B64)
    key = serialization.load_der_public_key(der)
    ct = key.encrypt(
        pw.encode("utf-8"),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return base64.b64encode(ct).decode("ascii")


def camel(label: str) -> str:
    parts = re.split(r"[\s_]+", label.strip())
    if not parts:
        return label
    return parts[0][0].lower() + parts[0][1:] + "".join(p.capitalize() for p in parts[1:])


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
    # default LIVE for create once body works — still gated
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
        pid, sig = p["id"], p["signature"]
        secure = {**auth, "x-secure-token": sig}
        day_term = next(t for t in (await client.get(ap.ORDER_TERMS, headers=auth)).json()["data"]["result"] if "DAY" in t["label"].upper())
        buy = next(s for s in (await client.get(ap.SECURITIES, headers=auth)).json()["data"] if s.get("symbol") == "JAIZBANK")
        hold = p["portfolioHoldings"][0]
        px = float(buy["price"])

        body: dict[str, Any] = {
            "portfolioId": pid,
            "securityId": buy["id"],
            "securityName": buy.get("secId") or buy.get("symbol"),
            "secId": buy.get("secId") or buy.get("symbol"),
            "symbol": buy.get("symbol"),
            "name": buy.get("secDesc") or buy.get("description"),
            "quantityRequested": 1,
            "orderType": "LIMIT",
            "priceType": "LIMIT",
            "price": px,
            "limitPrice": px,
            "orderTermId": day_term["id"],
            "orderTerm": day_term["name"],
            "orderTermLabel": day_term["label"],
            "timeInForce": day_term.get("fixTimeInForce"),
            "side": "BUY",
            "orderSide": "BUY",
            "currency": "NGN",
            "marketCode": "NGX",
            "board": "EQTY",
        }

        # value providers for missing labels
        def value_for(label: str, key: str) -> Any:
            l = label.lower()
            k = key.lower()
            if "security name" in l or k in {"securityname", "secname", "name"}:
                return buy.get("secId") or buy.get("symbol")
            if "security" in l and "id" in l:
                return buy["id"]
            if "symbol" in l or k == "symbol":
                return buy.get("symbol")
            if "quantity" in l or "qty" in k:
                return 1
            if "price type" in l or k == "pricetype":
                return "LIMIT"
            if "order type" in l or k == "ordertype":
                return "LIMIT"
            if "price" in l or "price" in k:
                return px
            if "term" in l or "term" in k:
                return day_term["id"] if "id" in k or "id" in l else day_term["name"]
            if "side" in l or "side" in k or "buy" in l:
                return "BUY"
            if "portfolio" in l:
                return pid
            if "board" in l:
                return "EQTY"
            if "market" in l:
                return "NGX"
            if "currency" in l:
                return "NGN"
            if "exchange" in l:
                return "NGX"
            if "account" in l:
                return p.get("accountNo") or p.get("tradingAccountNo")
            if "tif" in l or "time in force" in l or "lifetime" in l:
                return day_term.get("fixTimeInForce")
            if "amount" in l or "consideration" in l or "value" in l:
                return px
            return buy.get("symbol")

        winning = None
        for i in range(25):
            rr = await client.post(ap.CREATE_ORDER, headers=secure, json=body)
            try:
                jb = rr.json()
            except Exception:
                jb = {"raw": rr.text[:400]}
            msg = ""
            if isinstance(jb, dict):
                err = jb.get("error") or {}
                if isinstance(err, dict):
                    msg = str(err.get("message") or "")
                if not msg:
                    msg = str(jb.get("message") or jb)[:300]
            else:
                msg = str(jb)[:300]
            print(i, rr.status_code, msg[:180])
            log.append({"i": i, "status": rr.status_code, "msg": msg[:300], "body_keys": sorted(body.keys())})

            if rr.status_code in (200, 201):
                winning = body
                log.append({"success": redact(jb), "body": body})
                break
            if rr.status_code >= 500:
                break

            m = re.search(r"(.+?) is (?:a )?required", msg, re.I)
            if not m:
                # try alternate phrasings
                m = re.search(r"Missing[:\s]+(.+)$", msg, re.I)
            if not m:
                print("unhandled", msg)
                # try calculate with same body for comparison
                rr2 = await client.post(ap.CALCULATE_PRICE, headers=secure, json=body)
                print("calc same body", rr2.status_code, str(rr2.json())[:200])
                log.append({"calc_same": {"status": rr2.status_code, "msg": str(rr2.json())[:300]}})
                break

            label = m.group(1).strip().strip("'\"")
            # candidate keys
            keys = [
                camel(label),
                label.replace(" ", ""),
                label.replace(" ", "_").lower(),
                label[0].lower() + label[1:] if label else label,
            ]
            # known aliases
            aliases = {
                "Security Name": ["securityName", "secId", "symbol", "security", "name", "secName", "securityLabel"],
                "Order Side": ["side", "orderSide", "buySell", "action"],
                "Side": ["side", "orderSide"],
                "Order Term": ["orderTermId", "orderTerm", "termId", "orderTermName"],
                "Time In Force": ["timeInForce", "tif", "fixTimeInForce"],
                "Limit Price": ["limitPrice", "price", "orderPrice"],
                "Price": ["price", "limitPrice", "orderPrice", "requestedPrice"],
                "Security": ["securityId", "secId", "securityName"],
                "Portfolio": ["portfolioId"],
                "Account": ["accountNo", "tradingAccountNo", "accountId"],
                "Board": ["board", "marketBoard", "boardName"],
                "Market": ["marketCode", "marketId", "securityExchange"],
                "Currency": ["currency", "ccy"],
                "Exchange": ["securityExchange", "exchange", "marketCode"],
                "Order Validity": ["orderTermId", "orderValidity", "validity"],
                "Validity": ["orderTermId", "validity"],
                "Client Order Id": ["clientOrderId", "clOrdId", "reference"],
                "Reference": ["reference", "clientOrderId"],
            }
            keys = list(dict.fromkeys(aliases.get(label, []) + keys))

            filled = False
            for k in keys:
                if k in body and body[k] not in (None, "", 0):
                    # already present — try alternate value shapes
                    continue
                val = value_for(label, k)
                trial = {**body, k: val}
                rr3 = await client.post(ap.CREATE_ORDER, headers=secure, json=trial)
                try:
                    jb3 = rr3.json()
                except Exception:
                    jb3 = {"raw": rr3.text[:300]}
                msg3 = str((jb3.get("error") or {}).get("message") if isinstance(jb3, dict) else jb3)[:180]
                print("  try", k, "=", val, "->", rr3.status_code, msg3)
                log.append({"try": k, "val": val, "status": rr3.status_code, "msg": msg3})
                if rr3.status_code in (200, 201):
                    body = trial
                    winning = trial
                    filled = True
                    log.append({"success": redact(jb3), "body": trial})
                    break
                # progressed if the same required message is gone
                if rr3.status_code == 400 and label.lower() not in msg3.lower():
                    body = trial
                    filled = True
                    break
            if winning:
                break
            if not filled:
                # force-overwrite first alias even if present
                k = keys[0]
                val = value_for(label, k)
                # try several value alternatives
                alts = [val]
                if "name" in k.lower() or "security" in label.lower():
                    alts = [
                        buy.get("secId"),
                        buy.get("symbol"),
                        buy.get("name"),
                        buy.get("secDesc"),
                        buy.get("description"),
                        f"{buy.get('symbol')} - {buy.get('secDesc')}",
                    ]
                if "price" in label.lower():
                    alts = [px, str(px), {"currency": "NGN", "amount": px}, int(px * 100), round(px, 2)]
                progressed = False
                for alt in alts:
                    trial = {**body, k: alt}
                    rr3 = await client.post(ap.CREATE_ORDER, headers=secure, json=trial)
                    jb3 = rr3.json()
                    msg3 = str((jb3.get("error") or {}).get("message") if isinstance(jb3, dict) else jb3)[:180]
                    print("  overwrite", k, alt, "->", rr3.status_code, msg3)
                    log.append({"overwrite": k, "val": alt, "status": rr3.status_code, "msg": msg3})
                    if rr3.status_code in (200, 201):
                        body = trial
                        winning = trial
                        progressed = True
                        break
                    if rr3.status_code == 400 and label.lower() not in msg3.lower():
                        body = trial
                        progressed = True
                        break
                if winning:
                    break
                if not progressed:
                    print("stuck on", label)
                    break

        if winning:
            print("WINNING CREATE BODY keys", sorted(winning.keys()))
            print(json.dumps({k: v for k, v in winning.items() if k != "portfolioId"}, default=str)[:500])
            # Also try calculate with winning body
            rr = await client.post(ap.CALCULATE_PRICE, headers=secure, json=winning)
            print("calc with winning", rr.status_code, str(rr.json())[:240])
            log.append({"calc_winning": {"status": rr.status_code, "resp": redact(rr.json())}})

            if live:
                # already created if status 200 above — if we only discovered body without posting success in loop end:
                pass
            # Sell probe with same shape
            if live or True:
                # Only place sell/buy if last create actually succeeded
                last_ok = any(x.get("status") in (200, 201) and x.get("i") is not None for x in log)
                # check success entry
                succeeded = any("success" in x for x in log)
                if succeeded and live:
                    sell = dict(winning)
                    sell["side"] = "SELL"
                    sell["orderSide"] = "SELL"
                    sell["securityId"] = hold["securityId"]
                    sell["securityName"] = hold.get("symbol")
                    sell["secId"] = hold.get("symbol")
                    sell["symbol"] = hold.get("symbol")
                    sell["quantityRequested"] = 1
                    spx = float(hold.get("marketPrice") or 1)
                    for k in list(sell):
                        if "price" in k.lower():
                            if isinstance(sell[k], (int, float)):
                                sell[k] = spx
                            elif isinstance(sell[k], dict) and "amount" in sell[k]:
                                sell[k] = {**sell[k], "amount": spx}
                    rr = await client.post(ap.CREATE_ORDER, headers=secure, json=sell)
                    print("SELL", rr.status_code, str(rr.json())[:240])
                    log.append({"SELL": {"status": rr.status_code, "resp": redact(rr.json()), "req": sell}})
                elif succeeded:
                    print("Create succeeded in discovery; set LIVE_ORDER=1 to also place sell (buy already placed)")
        else:
            print("no winning create body")

    OUT.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
