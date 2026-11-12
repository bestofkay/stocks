#!/usr/bin/env python3
"""Resolve InvestNaija 'Price is required' after priceType is set."""

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

OUT = ROOT / "logs" / "investnaija_order_probe4.json"


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
        buy = next(s for s in (await client.get(ap.SECURITIES, headers=auth)).json()["data"] if s.get("symbol") == "JAIZBANK")
        hold = p["portfolioHoldings"][0]
        px = float(buy["price"])

        base = {
            "portfolioId": pid,
            "securityId": buy["id"],
            "quantityRequested": 1,
            "orderType": "LIMIT",
            "priceType": "LIMIT",
            "orderTermId": day_term["id"],
            "orderTerm": day_term["name"],
            "side": "BUY",
            "symbol": buy["symbol"],
        }

        price_variants: list[tuple[str, dict[str, Any]]] = [
            ("limitPrice_num", {"limitPrice": px}),
            ("price_num", {"price": px}),
            ("orderPrice", {"orderPrice": px}),
            ("requestedPrice", {"requestedPrice": px}),
            ("unitPrice", {"unitPrice": px}),
            ("px", {"px": px}),
            ("Price", {"Price": px}),
            ("limit_price", {"limit_price": px}),
            ("priceRequested", {"priceRequested": px}),
            ("requestedPx", {"requestedPx": px}),
            ("tradePrice", {"tradePrice": px}),
            ("execPrice", {"execPrice": px}),
            ("price_str", {"price": str(px)}),
            ("limitPrice_str", {"limitPrice": str(px)}),
            ("price_money", {"price": {"currency": "NGN", "amount": px}}),
            ("limitPrice_money", {"limitPrice": {"currency": "NGN", "amount": px}}),
            ("orderPrice_money", {"orderPrice": {"currency": "NGN", "amount": px}}),
            ("price_and_limit", {"price": px, "limitPrice": px}),
            ("price_money_and_type", {"price": {"currency": "NGN", "amount": px}, "limitPrice": px}),
            ("both_money", {"price": {"currency": "NGN", "amount": px}, "limitPrice": {"currency": "NGN", "amount": px}}),
            ("priceValue", {"priceValue": px}),
            ("limit", {"limit": px}),
            ("stopPrice", {"stopPrice": px, "price": px}),
            ("maxPrice", {"maxPrice": px}),
            ("bidPrice", {"bidPrice": px}),
            ("offerPrice", {"offerPrice": px}),
            ("lastPrice", {"lastPrice": px}),
            ("marketPrice", {"marketPrice": px}),
            ("price_int_kobo", {"price": int(px * 100)}),
            ("price_int", {"price": int(px)}),
        ]

        winning = None
        for name, extra in price_variants:
            body = {**base, **extra}
            rr = await client.post(ap.CALCULATE_PRICE, headers=secure, json=body)
            jb = rr.json()
            msg = str(jb)[:220]
            print(name, rr.status_code, msg)
            results.append({"step": name, "status": rr.status_code, "msg": msg, "req_extra": extra})
            if rr.status_code == 200:
                winning = body
                break
            if rr.status_code == 400 and "Price is required" not in msg and "Price Type" not in msg:
                print("  PROGRESS", msg)
                # keep this and try to fill remaining
                cur = body
                for _ in range(10):
                    rr2 = await client.post(ap.CALCULATE_PRICE, headers=secure, json=cur)
                    jb2 = rr2.json()
                    m2 = str(jb2)
                    print("  iter", rr2.status_code, m2[:180])
                    results.append({"step": f"progress:{name}", "status": rr2.status_code, "msg": m2[:220], "req": cur})
                    if rr2.status_code == 200:
                        winning = cur
                        break
                    import re
                    m = re.search(r"message': '([^']+)'", m2) or re.search(r'"message"\s*:\s*"([^"]+)"', m2)
                    if not m:
                        break
                    err = m.group(1)
                    if "required" not in err.lower():
                        break
                    field = err.replace(" is a required", "").replace(" is required", "").strip()
                    camel = field[0].lower() + "".join(part.capitalize() for part in field.split()[1:]) if " " in field else (field[:1].lower() + field[1:])
                    candidates = [camel, field.replace(" ", ""), field.replace(" ", "_").lower()]
                    # sensible values
                    advanced = False
                    for ck in candidates:
                        if ck in cur:
                            continue
                        if "term" in ck.lower():
                            val: Any = day_term["id"]
                        elif "side" in ck.lower():
                            val = "BUY"
                        elif "board" in ck.lower():
                            val = "EQTY"
                        elif "market" in ck.lower():
                            val = "NGX"
                        elif "amount" in ck.lower() or "value" in ck.lower() or "consideration" in ck.lower():
                            val = px
                        elif "price" in ck.lower():
                            val = px
                        elif "sec" in ck.lower():
                            val = buy["id"]
                        else:
                            val = 1
                        trial = {**cur, ck: val}
                        rr3 = await client.post(ap.CALCULATE_PRICE, headers=secure, json=trial)
                        jb3 = rr3.json()
                        m3 = str(jb3)
                        print("   fill", ck, val, "->", rr3.status_code, m3[:120])
                        results.append({"step": f"fill:{ck}", "status": rr3.status_code, "msg": m3[:180]})
                        if rr3.status_code == 200 or (rr3.status_code == 400 and field.lower() not in m3.lower()):
                            cur = trial
                            advanced = True
                            if rr3.status_code == 200:
                                winning = trial
                            break
                    if winning or not advanced:
                        break
                if winning:
                    break

        if winning:
            print("WINNING CALC", json.dumps({k: v for k, v in winning.items() if k != "portfolioId"}))
            results.append({"winning": winning})
            # try create even in dry mode with calculate only logged; live places
            if live:
                rr = await client.post(ap.CREATE_ORDER, headers=secure, json=winning)
                jb = rr.json()
                print("CREATE_BUY", rr.status_code, str(jb)[:300])
                results.append({"CREATE_BUY": {"status": rr.status_code, "resp": redact(jb), "req": winning}})
                sell = dict(winning)
                sell["side"] = "SELL"
                sell["securityId"] = hold["securityId"]
                sell["symbol"] = hold.get("symbol")
                sell["quantityRequested"] = 1
                spx = float(hold.get("marketPrice") or 1)
                for k in list(sell):
                    if "price" in k.lower() or k in {"limit", "px"}:
                        if isinstance(sell[k], dict) and "amount" in sell[k]:
                            sell[k] = {**sell[k], "amount": spx}
                        elif isinstance(sell[k], (int, float, str)):
                            sell[k] = type(sell[k])(spx) if not isinstance(sell[k], str) else str(spx)
                rr = await client.post(ap.CREATE_ORDER, headers=secure, json=sell)
                jb = rr.json()
                print("CREATE_SELL", rr.status_code, str(jb)[:300])
                results.append({"CREATE_SELL": {"status": rr.status_code, "resp": redact(jb), "req": sell}})
        else:
            print("no win")

    OUT.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
