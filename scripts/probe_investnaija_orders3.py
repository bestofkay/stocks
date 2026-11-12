#!/usr/bin/env python3
"""Find priceType / remaining required fields for InvestNaija calculate-price."""

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

OUT = ROOT / "logs" / "investnaija_order_probe3.json"


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
    results: list[Any] = []

    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        login = (await client.post(ap.LOGIN, json={"email": email, "password": encrypt_password(password), "rememberMe": True})).json()
        auth = {**headers, "Authorization": login["token"], "x-uuid-token": login["uuid_token"]}
        p = (await client.get(ap.TRADE_PORTFOLIOS, headers=auth)).json()["data"][0]
        pid, sig = p["id"], p["signature"]
        secure = {**auth, "x-secure-token": sig}
        day_term = next(t for t in (await client.get(ap.ORDER_TERMS, headers=auth)).json()["data"]["result"] if "DAY" in t["label"].upper())
        secs = {s.get("symbol"): s for s in (await client.get(ap.SECURITIES, headers=auth)).json()["data"]}
        buy = secs["JAIZBANK"]
        hold = p["portfolioHoldings"][0]
        px = float(buy["price"])

        price_type_keys = ["priceType", "Price Type", "price_type", "ordPriceType", "pxType", "pricingType"]
        price_type_vals = [
            "LIMIT", "Limit", "limit", "LMT", "MARKET", "Market", "MKT",
            "LIMITED", "FIXED", "BEST", "BEST_PRICE", "LIMIT_PRICE", "MARKET_PRICE",
            "L", "M", "1", "2", 1, 2, "LIMIT_ORDER", "MARKET_ORDER",
        ]

        base = {
            "portfolioId": pid,
            "securityId": buy["id"],
            "quantityRequested": 1,
            "orderType": "LIMIT",
            "limitPrice": px,
            "price": px,
            "orderTermId": day_term["id"],
            "orderTerm": day_term["name"],
            "side": "BUY",
            "symbol": buy["symbol"],
        }

        winning = None
        for pk in price_type_keys:
            for pv in price_type_vals:
                body = {**base, pk: pv}
                rr = await client.post(ap.CALCULATE_PRICE, headers=secure, json=body)
                jb = rr.json()
                msg = str(jb)
                short = msg[:180]
                print("pt", pk, pv, rr.status_code, short)
                results.append({"step": f"{pk}={pv}", "status": rr.status_code, "msg": short})
                if rr.status_code == 200:
                    winning = body
                    break
                # if message changed away from Price Type, keep exploring that branch
                if rr.status_code == 400 and "Price Type" not in msg:
                    print("  progressed:", short)
                    # try to continue filling from here by nesting another round of missing fields
                    winning_attempt = body
                    # iterative required-field filling using message
                    for _ in range(8):
                        rr2 = await client.post(ap.CALCULATE_PRICE, headers=secure, json=winning_attempt)
                        jb2 = rr2.json()
                        m2 = str(jb2)
                        print("  iter", rr2.status_code, m2[:160])
                        results.append({"step": "iter", "status": rr2.status_code, "req": winning_attempt, "msg": m2[:200]})
                        if rr2.status_code == 200:
                            winning = winning_attempt
                            break
                        # parse "X is required"
                        import re
                        m = re.search(r"'?([^']+?)'?\s+is (?:a )?required", m2, re.I)
                        if not m:
                            # try other error shapes
                            break
                        missing = m.group(1).strip()
                        # map label -> camelCase guesses
                        guesses = {
                            "Order Term": ["orderTermId", "orderTerm", "termId"],
                            "Order Side": ["side", "orderSide", "buySell"],
                            "Side": ["side", "orderSide"],
                            "Security": ["securityId", "secId", "symbol"],
                            "Portfolio": ["portfolioId"],
                            "Limit Price": ["limitPrice", "price"],
                            "Price": ["limitPrice", "price"],
                            "Amount": ["amount", "orderAmount", "value"],
                            "Board": ["board", "marketBoard"],
                            "Market": ["marketCode", "marketId"],
                        }
                        # also camelCase the missing label
                        camel = missing[0].lower() + "".join(w.capitalize() for w in missing.split()[1:]) if " " in missing else missing[0].lower() + missing[1:]
                        keys = guesses.get(missing, [camel, missing.replace(" ", ""), missing.replace(" ", "_").lower()])
                        filled = False
                        for k in keys:
                            if k in winning_attempt:
                                continue
                            # pick a sensible value
                            val: Any
                            if "term" in k.lower():
                                val = day_term["id"]
                            elif "side" in k.lower() or k == "buySell":
                                val = "BUY"
                            elif "price" in k.lower():
                                val = px
                            elif "board" in k.lower():
                                val = "EQTY"
                            elif "market" in k.lower():
                                val = "NGX"
                            elif "amount" in k.lower() or "value" in k.lower():
                                val = px
                            else:
                                val = buy["id"] if "sec" in k.lower() or "security" in k.lower() else 1
                            trial = {**winning_attempt, k: val}
                            rr3 = await client.post(ap.CALCULATE_PRICE, headers=secure, json=trial)
                            jb3 = rr3.json()
                            m3 = str(jb3)
                            print("   try", k, "=", val, "->", rr3.status_code, m3[:120])
                            results.append({"step": f"fill:{k}", "status": rr3.status_code, "msg": m3[:180]})
                            if rr3.status_code == 200 or (rr3.status_code == 400 and missing.lower() not in m3.lower()):
                                winning_attempt = trial
                                filled = True
                                if rr3.status_code == 200:
                                    winning = trial
                                break
                        if winning:
                            break
                        if not filled:
                            break
                    if winning:
                        break
            if winning:
                break

        if winning:
            print("WINNING", {k: v for k, v in winning.items() if k != "portfolioId"})
            results.append({"winning": winning})
            if live:
                rr = await client.post(ap.CREATE_ORDER, headers=secure, json=winning)
                jb = rr.json()
                print("CREATE_BUY", rr.status_code, str(jb)[:300])
                results.append({"CREATE_BUY": {"status": rr.status_code, "resp": redact(jb), "req": winning}})
                sell = dict(winning)
                sell["side"] = "SELL"
                if "orderSide" in sell:
                    sell["orderSide"] = "SELL"
                sell["securityId"] = hold["securityId"]
                sell["symbol"] = hold.get("symbol")
                sell["quantityRequested"] = 1
                spx = float(hold.get("marketPrice") or 1)
                sell["limitPrice"] = spx
                sell["price"] = spx
                rr = await client.post(ap.CREATE_ORDER, headers=secure, json=sell)
                jb = rr.json()
                print("CREATE_SELL", rr.status_code, str(jb)[:300])
                results.append({"CREATE_SELL": {"status": rr.status_code, "resp": redact(jb), "req": sell}})
        else:
            print("still failing")

    OUT.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
