#!/usr/bin/env python3
"""Retry create-order with tenant/channel headers and float serialization variants."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.connectors.investnaija import api_paths as ap  # noqa: E402

OUT = ROOT / "logs" / "investnaija_order_probe17.json"


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
        return str(jb)[:240]
    err = jb.get("error")
    if isinstance(err, dict):
        m = str(err.get("message") or "")
        if "Failed to read request" in m:
            return "JAVA_FAIL"
        return m[:240]
    if jb.get("code") == 200 or jb.get("status") in ("success", 200):
        return "SUCCESS:" + json.dumps(jb)[:220]
    return str(jb)[:240]


async def main() -> int:
    email = os.environ["IN_USER"].strip()
    password = os.environ["IN_PASS"]
    live = os.environ.get("LIVE_ORDER", "").strip().lower() in {"1", "true", "yes"}
    base_headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": ap.APP_ORIGIN,
        "Referer": ap.APP_ORIGIN + "/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
        "Content-Type": "application/json",
    }
    log: list[Any] = []

    async with httpx.AsyncClient(timeout=60.0, headers=base_headers) as client:
        login = (await client.post(ap.LOGIN, json={"email": email, "password": encrypt_password(password), "rememberMe": True})).json()
        auth = {**base_headers, "Authorization": login["token"], "x-uuid-token": login["uuid_token"]}
        p = (await client.get(ap.TRADE_PORTFOLIOS, headers=auth)).json()["data"][0]
        day_term = next(t for t in (await client.get(ap.ORDER_TERMS, headers=auth)).json()["data"]["result"] if "DAY" in t["label"].upper())
        buy = next(s for s in (await client.get(ap.SECURITIES, headers=auth)).json()["data"] if s.get("symbol") == "JAIZBANK")
        hold = p["portfolioHoldings"][0]
        px = round(float(buy["price"]), 2)

        body = {
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

        header_sets = [
            ("plain", {}),
            ("tenant_absa", {"x-tenant": "absa", "tenant": "absa"}),
            ("tenant_chd", {"x-tenant": "chd", "x-tenant-id": "chd"}),
            ("tenant_investnaija", {"x-tenant": "investnaija"}),
            ("channel_web", {"x-channel": "web", "channel": "web"}),
            ("request_id", {"x-request-id": str(uuid.uuid4()), "x-correlation-id": str(uuid.uuid4())}),
            ("zanibal", {"x-zanibal-tenant": "absa", "x-app": "investnaija"}),
            ("secure_plus_tenant", {"x-tenant": "absa", "x-channel": "WEB", "x-client": "investnaija"}),
            ("accept_problem", {"Accept": "application/problem+json, application/json"}),
        ]

        winning = None
        for hname, extra in header_sets:
            hdrs = {**auth, "x-secure-token": p["signature"], **extra}
            rr = await client.post(ap.CREATE_ORDER, headers=hdrs, json=body)
            msg = classify(rr.json())
            print("hdr", hname, rr.status_code, msg[:140])
            log.append({"hdr": hname, "status": rr.status_code, "msg": msg})
            if msg.startswith("SUCCESS") or rr.status_code in (200, 201):
                winning = (hname, body, rr.json(), hdrs)
                break

        # Raw JSON with prices as 2-decimal strings embedded without quotes? invalid.
        # Try Decimal encoded as JSON number with many decimals
        # Try body as array
        for bname, raw in [
            ("pretty", json.dumps(body, indent=2)),
            ("float_str_in_json_number", json.dumps(body).replace(str(px), f"{px:.2f}")),
            ("prices_as_ints_naira", json.dumps({**body, "marketPrice": int(px), "limitPrice": int(px)})),
        ]:
            rr = await client.post(
                ap.CREATE_ORDER,
                headers={**auth, "x-secure-token": p["signature"], "Content-Type": "application/json"},
                content=raw.encode(),
            )
            msg = classify(rr.json())
            print("raw", bname, rr.status_code, msg[:140])
            log.append({"raw": bname, "status": rr.status_code, "msg": msg})
            if msg.startswith("SUCCESS"):
                winning = (bname, body, rr.json(), auth)
                break

        # Try calculate-price then create with EXACT same headers + body that calc accepted,
        # plus include calc response under known keys the Java DTO might need
        calc_body = {**body, "price": px}
        calc = (await client.post(ap.CALCULATE_PRICE, headers={**auth, "x-secure-token": p["signature"]}, json=calc_body)).json()
        print("calc", calc.get("code"), calc.get("data"))

        # Maybe create expects only the fee preview token / hash?
        for bname, b in [
            ("calc_data_as_order", {**body, **{k: v for k, v in (calc.get("data") or {}).items() if k != "feesBreakdown"}}),
            ("with_breakdown", {**body, **(calc.get("data") or {})}),
        ]:
            rr = await client.post(ap.CREATE_ORDER, headers={**auth, "x-secure-token": p["signature"]}, json=b)
            msg = classify(rr.json())
            print("after_calc", bname, rr.status_code, msg[:140])
            log.append({"after_calc": bname, "status": rr.status_code, "msg": msg})

        if winning:
            print("WINNING", winning[0])
            log.append({"winning": winning[0], "resp": winning[2]})
            if live:
                sell = dict(body)
                sell["side"] = "SELL"
                sell["securityId"] = hold["securityId"]
                sell["securityName"] = hold.get("symbol")
                spx = round(float(hold.get("marketPrice") or 1), 2)
                sell["marketPrice"] = spx
                sell["limitPrice"] = spx
                rr = await client.post(ap.CREATE_ORDER, headers=winning[3] if len(winning) > 3 else {**auth, "x-secure-token": p["signature"]}, json=sell)
                print("SELL", rr.status_code, classify(rr.json())[:200])
                log.append({"SELL": rr.json()})

    OUT.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
