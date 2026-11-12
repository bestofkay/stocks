#!/usr/bin/env python3
"""Probe InvestNaija calculate-price / create-order with observed field shapes."""

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

OUT = ROOT / "logs" / "investnaija_order_probe.json"


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
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(x in lk for x in ("token", "signature", "password", "authorization")) and isinstance(v, str) and v:
                out[k] = v[:10] + "...[REDACTED]"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


async def main() -> int:
    email = os.environ["IN_USER"].strip()
    password = os.environ["IN_PASS"]
    dry = os.environ.get("LIVE_ORDER", "").strip() not in {"1", "true", "yes"}
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": ap.APP_ORIGIN,
        "Referer": ap.APP_ORIGIN + "/",
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json",
    }
    results: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        login = (await client.post(ap.LOGIN, json={"email": email, "password": encrypt_password(password), "rememberMe": True})).json()
        token, uuid_token = login["token"], login["uuid_token"]
        auth = {**headers, "Authorization": token, "x-uuid-token": uuid_token}

        portfolios = (await client.get(ap.TRADE_PORTFOLIOS, headers=auth)).json()["data"]
        p = portfolios[0]
        pid = p["id"]
        sig = p["signature"]
        holdings = p.get("portfolioHoldings") or []
        print("portfolio", p.get("accountNo"), "cash", p.get("availableCash"), "holdings", len(holdings))
        for h in holdings:
            print(" holding", h.get("symbol"), "qty", h.get("quantity"), "px", h.get("marketPrice"), "sec", h.get("securityId"))

        secure = {**auth, "x-secure-token": sig}
        terms = (await client.get(ap.ORDER_TERMS, headers=auth)).json()["data"]["result"]
        day_term = next(t for t in terms if "DAY" in (t.get("label") or "").upper())
        print("day_term", day_term)

        # Find a cheap equity for buy probe (price under ~50, active)
        secs = (await client.get(ap.SECURITIES, headers=auth)).json()["data"]
        # Prefer something liquid-ish; use ACCESSCORP if held, else cheap stock
        by_sym = {s.get("symbol") or s.get("secId"): s for s in secs}
        buy_sym = "JAIZBANK" if "JAIZBANK" in by_sym else "FIDELITYBK" if "FIDELITYBK" in by_sym else "GTCO"
        buy_sec = by_sym.get(buy_sym) or secs[0]
        print("buy_sec", buy_sec.get("symbol"), buy_sec.get("price"), buy_sec.get("id"))

        hold = holdings[0] if holdings else None

        # Candidate calculate-price bodies
        calc_candidates = [
            {
                "name": "calc_a",
                "body": {
                    "portfolioId": pid,
                    "securityId": buy_sec["id"],
                    "symbol": buy_sec.get("symbol"),
                    "side": "BUY",
                    "quantity": 1,
                    "orderType": "LIMIT",
                    "limitPrice": buy_sec.get("price") or buy_sec.get("lastPx") or 1,
                    "orderTermId": day_term["id"],
                    "orderTerm": day_term["name"],
                },
            },
            {
                "name": "calc_b",
                "body": {
                    "portfolioId": pid,
                    "secId": buy_sec.get("secId") or buy_sec.get("symbol"),
                    "side": "BUY",
                    "qty": 1,
                    "price": buy_sec.get("price") or 1,
                    "orderTermId": day_term["id"],
                    "timeInForce": day_term.get("fixTimeInForce"),
                },
            },
            {
                "name": "calc_c",
                "body": {
                    "accountId": pid,
                    "securityId": buy_sec["id"],
                    "orderSide": "BUY",
                    "orderQty": 1,
                    "limitPrice": float(buy_sec.get("price") or 1),
                    "orderTermId": day_term["id"],
                },
            },
            {
                "name": "calc_d",
                "body": {
                    "portfolio_id": pid,
                    "security_id": buy_sec["id"],
                    "side": "buy",
                    "quantity": 1,
                    "price": float(buy_sec.get("price") or 1),
                    "term_id": day_term["id"],
                },
            },
        ]

        for c in calc_candidates:
            for url_name, url in (("CALCULATE_PRICE", ap.CALCULATE_PRICE),):
                rr = await client.post(url, headers=secure, json=c["body"])
                try:
                    jb = rr.json()
                except Exception:
                    jb = {"raw": rr.text[:500]}
                entry = {"probe": c["name"], "url": url_name, "status": rr.status_code, "req": c["body"], "resp": redact(jb)}
                results.append(entry)
                print(c["name"], url_name, rr.status_code, str(jb)[:180])

        # Also try without secure token / with GET variants
        rr = await client.post(ap.CALCULATE_PRICE, headers=auth, json=calc_candidates[0]["body"])
        results.append({"probe": "calc_a_no_secure", "status": rr.status_code, "resp": redact(rr.json() if rr.content else None)})
        print("calc_a_no_secure", rr.status_code)

        # Probe alternate calculate URLs
        alt_urls = [
            f"{ap.API_BASE}/trades/trade-order/calculate",
            f"{ap.API_BASE}/trades/orders/calculate-price",
            f"{ap.API_BASE}/trades/orders/preview",
            f"{ap.API_BASE}/trades/order/calculate-price",
            f"{ap.API_BASE}/trades/calculate-price",
        ]
        for url in alt_urls:
            rr = await client.post(url, headers=secure, json=calc_candidates[0]["body"])
            try:
                jb = rr.json()
            except Exception:
                jb = {"raw": rr.text[:300]}
            if rr.status_code != 404:
                results.append({"probe": "alt:" + url.split("/")[-1], "status": rr.status_code, "resp": redact(jb)})
                print("alt", url.split("/")[-1], rr.status_code, str(jb)[:160])

        # If calculate worked, optionally create (still dry by default: use invalid tiny or stop)
        good = next((r for r in results if r.get("status") == 200 and "calc" in r.get("probe", "")), None)
        if good and not dry:
            create_body = dict(good["req"])
            # mirror create
            rr = await client.post(ap.CREATE_ORDER, headers=secure, json=create_body)
            try:
                jb = rr.json()
            except Exception:
                jb = {"raw": rr.text[:500]}
            results.append({"probe": "LIVE_CREATE_BUY", "status": rr.status_code, "req": create_body, "resp": redact(jb)})
            print("LIVE_CREATE_BUY", rr.status_code, str(jb)[:200])

            if hold and (hold.get("quantity") or 0) >= 1:
                sell_body = {
                    "portfolioId": pid,
                    "securityId": hold.get("securityId"),
                    "symbol": hold.get("symbol"),
                    "side": "SELL",
                    "quantity": 1,
                    "orderType": "LIMIT",
                    "limitPrice": hold.get("marketPrice") or 1,
                    "orderTermId": day_term["id"],
                    "orderTerm": day_term["name"],
                }
                # try same shape that worked
                for k in list(create_body.keys()):
                    if k not in sell_body and k in ("secId", "qty", "price", "orderQty", "orderSide", "timeInForce", "accountId"):
                        pass
                # Prefer overlaying known working keys onto sell
                working = {**create_body}
                working["side"] = working.get("side", "BUY")
                if "side" in working:
                    working["side"] = "SELL"
                if "orderSide" in working:
                    working["orderSide"] = "SELL"
                working["securityId"] = hold.get("securityId")
                if "secId" in working:
                    working["secId"] = hold.get("symbol")
                if "symbol" in working:
                    working["symbol"] = hold.get("symbol")
                working["quantity"] = working.get("quantity", 1)
                if "qty" in working:
                    working["qty"] = 1
                if "orderQty" in working:
                    working["orderQty"] = 1
                px = hold.get("marketPrice") or 1
                if "limitPrice" in working:
                    working["limitPrice"] = px
                if "price" in working:
                    working["price"] = px
                rr = await client.post(ap.CREATE_ORDER, headers=secure, json=working)
                try:
                    jb = rr.json()
                except Exception:
                    jb = {"raw": rr.text[:500]}
                results.append({"probe": "LIVE_CREATE_SELL", "status": rr.status_code, "req": working, "resp": redact(jb)})
                print("LIVE_CREATE_SELL", rr.status_code, str(jb)[:200])
        elif dry:
            print("dry-run: set LIVE_ORDER=1 to place after a successful calculate")

        # Also dump full wallet + portfolio virtual account fields for deposits
        wallet = (await client.get(ap.WALLET_BALANCE, headers=auth)).json()
        results.append({
            "probe": "deposit_fields",
            "wallet_vNuban": wallet.get("vNuban"),
            "wallet_balance": wallet.get("balance"),
            "portfolio_virtualAccountNo": p.get("virtualAccountNo"),
            "portfolio_virtualAccountBankName": p.get("virtualAccountBankName"),
            "portfolio_accountNo": p.get("accountNo"),
        })

    OUT.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
