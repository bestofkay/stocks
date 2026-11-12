#!/usr/bin/env python3
"""Isolate Java deserialization failure among CHD-valid create fields."""

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

OUT = ROOT / "logs" / "investnaija_order_probe12.json"


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
        return str(jb)[:160]
    err = jb.get("error")
    if isinstance(err, dict):
        m = str(err.get("message") or "")
        if "Failed to read request" in m:
            return "JAVA_FAIL"
        return m[:160]
    if jb.get("code") == 200 or jb.get("status") in ("success", 200):
        return "SUCCESS"
    return str(jb)[:160]


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
        st, msg, jb = await post(core)
        print("core", st, msg)
        log.append({"core": msg})

        # Remove one price field at a time
        for drop in ("marketPrice", "price", "limitPrice"):
            body = {k: v for k, v in core.items() if k != drop}
            st, msg, _ = await post(body)
            print("drop", drop, st, msg)
            log.append({"drop": drop, "msg": msg})

        # Replace each field with alternate types while keeping CHD happy
        # Focus on fields that might break Jackson: orderTermId, portfolioId, securityId, prices
        trials: list[tuple[str, dict]] = []

        # orderTermId as string
        trials.append(("termId_str", {**core, "orderTermId": str(day_term["id"])}))
        # omit orderTermId (keep name)
        trials.append(("no_termId", {k: v for k, v in core.items() if k != "orderTermId"}))
        # use orderTerm label/name variants
        trials.append(("term_label", {**core, "orderTermName": day_term["label"]}))
        trials.append(("term_fix", {**core, "orderTermName": day_term.get("fixTimeInForce")}))

        # price as only limitPrice+marketPrice without `price` — already tested drop
        # Try sending prices as integers (whole naira)
        trials.append(("prices_int", {**core, "marketPrice": int(px), "price": int(px), "limitPrice": int(px)}))
        # Try high precision
        trials.append(("prices_4dp", {**core, "marketPrice": round(px, 4), "price": round(px, 4), "limitPrice": round(px, 4)}))

        # Maybe Java wants `ordType` / FIX fields and chokes on camelCase extras — strip to absolute min that passes CHD
        # Discover absolute min for JAVA_FAIL (i.e. past CHD)
        # From drops: need all three prices. Confirm.
        min_java = dict(core)
        trials.append(("min_java", min_java))

        # Change portfolioId to accountNo?
        trials.append(("pid_accountNo", {**core, "portfolioId": p.get("accountNo")}))
        trials.append(("pid_cash", {**core, "portfolioId": p.get("cashAccountId")}))
        # encrypted id from list vs detail
        detail = (await client.get(ap.trade_portfolio(p["id"]), headers=secure)).json().get("data") or {}
        if detail.get("portfolioId"):
            trials.append(("pid_detail", {**core, "portfolioId": detail["portfolioId"]}))

        # securityId as secId ticker
        trials.append(("sec_ticker", {**core, "securityId": buy["secId"]}))

        # side + orderSide
        trials.append(("orderSide", {**core, "orderSide": "BUY"}))

        # Maybe the Java DTO uses `limit` not limitPrice, and limitPrice number breaks unknown property
        # if Jackson FAIL_ON_UNKNOWN_PROPERTIES — try ONLY fields Java might know,
        # but we must keep CHD validators happy. Unless we can find an endpoint that skips CHD validation.

        # Try wrapping numbers in strings for Java BigDecimal while somehow... CHD rejects.
        # Unless CHD only validates typeof for limitPrice and we pass limitPrice as number
        # and other price fields as money for Java? 
        trials.append(("mp_num_price_money", {**core, "price": {"currency": "NGN", "amount": px}}))
        trials.append(("price_num_lp_money", {**core, "limitPrice": {"currency": "NGN", "amount": px}}))
        trials.append(("mp_money_others_num", {**core, "marketPrice": {"currency": "NGN", "amount": px}}))

        # Jackson unknown property: remove CHD-only fields one by one from core while adding nothing
        # If removing a field goes back to CHD error, that field is required by CHD.
        # If removing fixes JAVA_FAIL, that field breaks Java!
        for drop in list(core.keys()):
            if drop in {"portfolioId", "securityId", "securityName", "quantityRequested", "orderType", "priceType", "side", "limitPrice", "price", "marketPrice"}:
                # still try
                pass
            body = {k: v for k, v in core.items() if k != drop}
            trials.append((f"without_{drop}", body))

        winning = None
        for name, body in trials:
            st, msg, jb = await post(body)
            print(name, st, msg)
            log.append({"name": name, "status": st, "msg": msg, "keys": sorted(body.keys())})
            if st in (200, 201) or msg == "SUCCESS":
                winning = (name, body, jb)
                break
            # any non-JAVA non-required progress
            if msg not in {"JAVA_FAIL"} and "required" not in msg.lower() and "No strings allowed" not in msg and "Invalid" not in msg:
                print("  INTERESTING", msg)
                winning = (name, body, jb)
                break

        if winning:
            name, body, jb = winning
            print("WINNING", name, json.dumps({k: v for k, v in body.items() if k != "portfolioId"}))
            log.append({"winning": {"name": name, "body": {k: v for k, v in body.items() if k != "portfolioId"}, "resp": jb}})
            if live:
                sell = dict(body)
                sell["side"] = "SELL"
                sell["securityId"] = hold["securityId"]
                sell["securityName"] = hold.get("symbol")
                sell["quantityRequested"] = 1
                spx = round(float(hold.get("marketPrice") or 1), 2)
                for k in ("marketPrice", "price", "limitPrice"):
                    if k in sell and isinstance(sell[k], (int, float)):
                        sell[k] = spx
                st, msg, jb = await post(sell)
                print("SELL", st, msg)
                log.append({"SELL": {"status": st, "msg": msg, "resp": jb}})
        else:
            # Last resort: capture full error HTML/body with more detail headers
            rr = await client.post(ap.CREATE_ORDER, headers={**secure, "Accept": "*/*"}, json=core)
            print("raw", rr.status_code, rr.text[:500])
            log.append({"raw": rr.text[:500]})

    OUT.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
