#!/usr/bin/env python3
"""Drop/coerce fields on an authenticated create (matching id+sig) to clear JAVA_FAIL."""

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

OUT = ROOT / "logs" / "investnaija_order_probe16.json"


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

        detail = (await client.get(ap.trade_portfolio(p["id"]), headers=secure)).json()["data"]
        print("list.id", p["id"][:30], "detail.id", str(detail.get("id"))[:30], "detail.portfolioId", str(detail.get("portfolioId"))[:30])
        print("detail.signature == list.signature", detail.get("signature") == p["signature"])

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
            "limitPrice": px,
        }

        async def post(body: dict) -> tuple[int, str, Any]:
            rr = await client.post(ap.CREATE_ORDER, headers=secure, json=body)
            jb = rr.json()
            return rr.status_code, classify(jb), jb

        async def post_sig(sig: str, body: dict):
            rr = await client.post(ap.CREATE_ORDER, headers={**auth, "x-secure-token": sig}, json=body)
            jb = rr.json()
            return rr.status_code, classify(jb), jb

        # Use detail.id as portfolioId with detail.signature
        if detail.get("id") and detail.get("signature"):
            b = {**core, "portfolioId": detail["id"]}
            st, msg, jb = await post_sig(detail["signature"], b)
            print("detail.id+detail.sig", st, msg[:160])
            log.append({"detail_id": msg})

        # Coerce each field type on authenticated request
        coercions = [
            ("orderTermId_str", {**core, "orderTermId": str(day_term["id"])}),
            ("orderTermId_omit", {k: v for k, v in core.items() if k != "orderTermId"}),
            ("orderTermName_omit", {k: v for k, v in core.items() if k != "orderTermName"}),
            ("orderTerm_obj", {**core, "orderTerm": {"id": day_term["id"], "name": day_term["name"]}, "orderTermId": day_term["id"]}),
            ("qty_float", {**core, "quantityRequested": 1.0}),
            ("side_Buy", {**core, "side": "Buy"}),
            ("securityId_ticker", {**core, "securityId": buy["secId"]}),
            ("securityId_omit", {k: v for k, v in core.items() if k != "securityId"}),
            ("prices_only_lp", {k: v for k, v in core.items() if k != "marketPrice"}),  # may CHD fail
            ("prices_only_mp", {k: v for k, v in core.items() if k != "limitPrice"}),
            ("add_accountNo", {**core, "accountNo": p.get("accountNo")}),
            ("add_board", {**core, "board": "EQTY", "marketCode": "NGX", "currency": "NGN"}),
            ("add_tif", {**core, "timeInForce": "DAY", "fixTimeInForce": "GOOD_TILL_DATE"}),
            ("orderType_Limit", {**core, "orderType": "Limit", "priceType": "Limit"}),
            ("no_priceType", {k: v for k, v in core.items() if k != "priceType"}),
            ("no_orderType", {k: v for k, v in core.items() if k != "orderType"}),
            # Try nested money ONLY for a field CHD might not typecheck — e.g. consideration
            ("add_consideration_num", {**core, "consideration": px, "totalConsideration": px, "amount": px}),
            ("add_consideration_money", {**core, "consideration": {"currency": "NGN", "amount": px}}),
            # Maybe Java wants security as object
            ("security_obj", {**core, "security": {"id": buy["id"], "secId": buy["secId"], "name": buy["secId"]}}),
            # portfolio as object
            ("portfolio_obj", {**{k: v for k, v in core.items() if k != "portfolioId"}, "portfolio": {"id": p["id"]}, "portfolioId": p["id"]}),
        ]

        winning = None
        for name, body in coercions:
            st, msg, jb = await post_sig(p["signature"], body)
            print(name, st, msg[:160])
            log.append({"name": name, "status": st, "msg": msg})
            if msg.startswith("SUCCESS") or st in (200, 201):
                winning = (name, body, jb)
                break
            if msg != "JAVA_FAIL" and "required" not in msg.lower() and "Please login" not in msg and "No strings allowed" not in msg and "Invalid" not in msg:
                print("  INTERESTING")
                winning = (name, body, jb)
                # continue for SUCCESS but record

        # Try create via PUT / PATCH
        for method in ("PUT", "PATCH"):
            rr = await client.request(method, ap.CREATE_ORDER, headers=secure, json=core)
            try:
                jb = rr.json()
            except Exception:
                jb = {"raw": rr.text[:200]}
            print(method, rr.status_code, classify(jb)[:120])
            log.append({"method": method, "status": rr.status_code, "msg": classify(jb)})

        # Try alternate create paths that might skip broken proxy mapping
        for url in [
            f"{ap.API_BASE}/trades/trade-order",
            f"{ap.API_BASE}/trades/portfolios/{p['id']}/orders",
            f"{ap.API_BASE}/trades/portfolios/{p['id']}/trade-order/create",
            f"{ap.API_BASE}/trades/trade-order/create/",
            f"{ap.API_BASE}/trades/trade-orders/create",
            f"{ap.API_BASE}/trades/orders/create",
            f"{ap.API_BASE}/trades/order",
        ]:
            rr = await client.post(url, headers=secure, json=core)
            if rr.status_code == 404:
                continue
            try:
                jb = rr.json()
            except Exception:
                jb = {"raw": rr.text[:200]}
            msg = classify(jb)
            print("url", url.split("/api/v1")[-1], rr.status_code, msg[:140])
            log.append({"url": url, "status": rr.status_code, "msg": msg})
            if msg.startswith("SUCCESS") or rr.status_code in (200, 201):
                winning = ("url:"+url, core, jb)
                break

        if winning and (isinstance(winning[2], dict) and (winning[2].get("code") == 200 or str(winning[0]).startswith("url"))):
            print("possible win", winning[0])
        if winning and isinstance(winning[2], dict) and (winning[2].get("code") == 200 or winning[2].get("status") == "success"):
            print("WINNING", winning[0])
            log.append({"winning": {"name": winning[0], "resp": winning[2]}})
            if live:
                sell = dict(winning[1])
                sell["side"] = "SELL"
                sell["securityId"] = hold["securityId"]
                sell["securityName"] = hold.get("symbol")
                spx = round(float(hold.get("marketPrice") or 1), 2)
                sell["marketPrice"] = spx
                sell["limitPrice"] = spx
                st, msg, jb = await post_sig(p["signature"], sell)
                print("SELL", st, msg)
                log.append({"SELL": {"status": st, "msg": msg, "resp": jb}})

    OUT.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
