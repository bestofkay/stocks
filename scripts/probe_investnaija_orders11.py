#!/usr/bin/env python3
"""Binary-search which create-order field breaks Java deserialization."""

from __future__ import annotations

import asyncio
import base64
import copy
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

OUT = ROOT / "logs" / "investnaija_order_probe11.json"


def encrypt_password(pw: str) -> str:
    der = base64.b64decode(ap.PASSWORD_PUBLIC_KEY_B64)
    key = serialization.load_der_public_key(der)
    ct = key.encrypt(
        pw.encode("utf-8"),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return base64.b64encode(ct).decode("ascii")


def msg_of(jb: Any) -> str:
    if not isinstance(jb, dict):
        return str(jb)[:240]
    err = jb.get("error")
    if isinstance(err, dict) and err.get("message"):
        m = str(err["message"])
        if m.startswith("{") and "Failed to read" in m:
            return "JAVA_FAIL"
        return m[:200]
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

        # Minimal that reaches Java (has numeric limitPrice)
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
            "limitPrice": px,
        }

        async def post(body: dict) -> tuple[int, str, Any]:
            rr = await client.post(ap.CREATE_ORDER, headers=secure, json=body)
            jb = rr.json()
            return rr.status_code, msg_of(jb), jb

        st, msg, _ = await post(base)
        print("base", st, msg)
        log.append({"base": msg})

        # Drop optional-looking fields one at a time from an extended body
        extended = {
            **base,
            "marketPrice": px,
            "price": px,
            "symbol": buy["symbol"],
            "secId": buy["secId"],
            "marketCode": "NGX",
            "board": "EQTY",
            "currency": "NGN",
            "orderSide": "BUY",
            "accountNo": p.get("accountNo"),
            "tradingAccountNo": p.get("tradingAccountNo"),
            "cashAccountId": p.get("cashAccountId"),
            "timeInForce": day_term.get("fixTimeInForce"),
            "orderTerm": day_term["name"],
            "orderTermLabel": day_term["label"],
        }
        st, msg, _ = await post(extended)
        print("extended", st, msg)
        log.append({"extended": msg})

        # Type coercions on each base field
        coercions: dict[str, list[Any]] = {
            "orderTermId": [str(day_term["id"]), day_term["id"], float(day_term["id"])],
            "quantityRequested": [1, 1.0, "1"],
            "limitPrice": [px, float(px), round(px, 2), px + 0.0],
            "orderType": ["LIMIT", "Limit", "LMT", "limit"],
            "priceType": ["LIMIT", "Limit", "LMT", "limit"],
            "side": ["BUY", "Buy", "buy", "B"],
            "securityName": [buy["secId"], buy["symbol"], buy.get("secDesc"), buy.get("name")],
            "securityId": [buy["id"], buy["secId"], buy["symbol"]],
        }

        for field, vals in coercions.items():
            for val in vals:
                body = {**base, field: val}
                st, msg, jb = await post(body)
                marker = "OK" if st in (200, 201) else ("CHD" if msg not in {"JAVA_FAIL"} and "required" in msg.lower() else ("JAVA" if msg == "JAVA_FAIL" else msg[:80]))
                if marker != "JAVA":
                    print("coerce", field, repr(val), st, marker if marker != "CHD" else msg[:100])
                    log.append({"coerce": field, "val": val, "status": st, "msg": msg})
                if st in (200, 201):
                    print("WINNING", body)
                    log.append({"winning": {k: v for k, v in body.items() if k != "portfolioId"}, "resp": jb})
                    if live:
                        sell = dict(body)
                        sell.update({
                            "side": "SELL",
                            "securityId": hold["securityId"],
                            "securityName": hold.get("symbol"),
                            "quantityRequested": 1,
                            "limitPrice": round(float(hold.get("marketPrice") or 1), 2),
                            "marketPrice": round(float(hold.get("marketPrice") or 1), 2),
                            "price": round(float(hold.get("marketPrice") or 1), 2),
                        })
                        st2, msg2, jb2 = await post(sell)
                        print("SELL", st2, msg2)
                        log.append({"SELL": {"status": st2, "msg": msg2, "resp": jb2}})
                    OUT.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
                    return 0

        # Try renaming limitPrice -> alternate while keeping a dummy limitPrice number
        # to satisfy CHD, plus Java-friendly field
        for alt in ["limit", "px", "orderPx", "lmtPx", "priceValue", "unitPrice", "ordPx", "fixPx", "maxShow"]:
            for alt_val in [px, {"currency": "NGN", "amount": px}]:
                body = {**base, alt: alt_val}
                st, msg, jb = await post(body)
                if msg != "JAVA_FAIL":
                    print("alt", alt, type(alt_val).__name__, st, msg[:120])
                    log.append({"alt": alt, "status": st, "msg": msg})
                if st in (200, 201):
                    log.append({"winning": body, "resp": jb})
                    OUT.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
                    print("WINNING alt")
                    return 0

        # Maybe portfolioId should be portfolioId from detail (different encoding)?
        detail = (await client.get(ap.trade_portfolio(p["id"]), headers=secure)).json()
        d = detail.get("data") if isinstance(detail, dict) else None
        if isinstance(d, dict) and d.get("portfolioId"):
            body = {**base, "portfolioId": d["portfolioId"]}
            st, msg, jb = await post(body)
            print("detail_portfolioId", st, msg[:120])
            log.append({"detail_pid": msg})
            if st in (200, 201):
                log.append({"winning": body, "resp": jb})

        # Try side-specific enums used by FIX: 1=buy 2=sell
        for side in [1, 2, "1", "2"]:
            body = {**base, "side": side}
            st, msg, _ = await post(body)
            if msg != "JAVA_FAIL":
                print("side", side, st, msg[:100])
                log.append({"side": side, "msg": msg})

        print("no win")

    OUT.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
