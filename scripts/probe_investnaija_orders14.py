#!/usr/bin/env python3
"""Create-order with UUID-style portfolio/account identifiers."""

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

OUT = ROOT / "logs" / "investnaija_order_probe14.json"


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
    if jb.get("code") == 200 or jb.get("status") in ("success", 200) or jb.get("success") is True:
        return "SUCCESS:" + json.dumps(jb)[:200]
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
        # Use encrypted id for x-secure-token route, but try alternate portfolioId values in body
        secure = {**auth, "x-secure-token": p["signature"]}
        day_term = next(t for t in (await client.get(ap.ORDER_TERMS, headers=auth)).json()["data"]["result"] if "DAY" in t["label"].upper())
        buy = next(s for s in (await client.get(ap.SECURITIES, headers=auth)).json()["data"] if s.get("symbol") == "JAIZBANK")
        hold = p["portfolioHoldings"][0]
        px = round(float(buy["price"]), 2)

        detail = (await client.get(ap.trade_portfolio(p["id"]), headers=secure)).json().get("data") or {}
        print("list id prefix", p["id"][:20])
        print("detail portfolioId prefix", str(detail.get("portfolioId"))[:20])
        print("cashAccountId", p.get("cashAccountId"))
        print("customerId", p.get("customerId"))
        print("clientId", p.get("clientId"))
        print("accountNo", p.get("accountNo"))
        print("tradingAccountNo", p.get("tradingAccountNo"))
        print("productId", p.get("productId"))
        print("refCode", p.get("refCode"))
        print("name", p.get("name"))

        async def post(body: dict) -> tuple[int, str, Any]:
            rr = await client.post(ap.CREATE_ORDER, headers=secure, json=body)
            jb = rr.json()
            return rr.status_code, classify(jb), jb

        base_fields = {
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

        candidates = [
            ("list_id", p["id"]),
            ("detail_pid", detail.get("portfolioId")),
            ("cashAccountId", p.get("cashAccountId")),
            ("customerId", p.get("customerId")),
            ("clientId", p.get("clientId")),
            ("accountNo", p.get("accountNo")),
            ("tradingAccountNo", p.get("tradingAccountNo")),
            ("productId", p.get("productId")),
            ("name", p.get("name")),
            ("refCode", p.get("refCode")),
            ("glAccountId", p.get("glAccountId")),
            ("groupId", p.get("groupId")),
            ("branchId", p.get("branchId")),
            ("login_profile_id", login.get("data", {}).get("id") if isinstance(login.get("data"), dict) else None),
        ]

        # Also dual: portfolioId=encrypted for CHD routing + accountId=uuid for Java
        extras_sets = [
            {},
            {"accountId": p.get("cashAccountId")},
            {"accountNo": p.get("accountNo")},
            {"tradingAccountNo": p.get("tradingAccountNo")},
            {"cashAccountId": p.get("cashAccountId")},
            {"clientId": p.get("clientId")},
            {"customerId": p.get("customerId")},
        ]

        winning = None
        for cname, pid in candidates:
            if not pid:
                continue
            for ei, extra in enumerate(extras_sets):
                body = {**base_fields, "portfolioId": pid, **{k: v for k, v in extra.items() if v}}
                st, msg, jb = await post(body)
                tag = f"{cname}+extra{ei}"
                interesting = msg != "JAVA_FAIL"
                if interesting or cname in {"list_id", "cashAccountId", "accountNo", "customerId"}:
                    print(tag, st, msg[:160])
                log.append({"tag": tag, "status": st, "msg": msg, "pid_kind": cname, "extra": extra})
                if msg.startswith("SUCCESS") or st in (200, 201):
                    winning = (tag, body, jb)
                    break
                # progress: not java fail and not generic login redirect
                if interesting and "Please login" not in msg and "required" not in msg.lower() and "Invalid" not in msg:
                    print("  PROGRESS", msg)
                    winning = (tag, body, jb)  # may still be error but useful
                    # don't break unless success — keep searching for SUCCESS
            if winning and str(winning[2].get("code") if isinstance(winning[2], dict) else "").startswith("200") or (
                winning and isinstance(winning[2], dict) and winning[2].get("status") in ("success", 200)
            ):
                break
            if winning and (winning[0].endswith and False):
                break
            # reset non-success winning marker
            if winning and not (isinstance(winning[2], dict) and (winning[2].get("code") == 200 or winning[2].get("status") == "success")):
                if "SUCCESS" not in str(winning[1]):
                    # only keep searching
                    if not (isinstance(winning[2], dict) and winning[2].get("status") in (200, "success")):
                        pass

        # Explicit success check loop result
        success_entry = next((x for x in log if str(x.get("msg", "")).startswith("SUCCESS") or x.get("status") in (200, 201)), None)
        if success_entry:
            print("FOUND SUCCESS", success_entry)
        else:
            # Print non-JAVA non-required messages
            other = [x for x in log if x.get("msg") not in {"JAVA_FAIL"} and "required" not in str(x.get("msg", "")).lower()]
            print("non-java msgs sample:")
            for x in other[:20]:
                print(" ", x["tag"], x["status"], x["msg"][:120])

        # If we got a business error (not JAVA_FAIL), try to complete with list_id in header path
        # by sending portfolioId=cashAccountId which got past Jackson before with accountNo
        for pid_name, pid in [("cashAccountId", p.get("cashAccountId")), ("customerId", p.get("customerId")), ("clientId", p.get("clientId"))]:
            body = {
                **base_fields,
                "portfolioId": pid,
                "accountNo": p.get("accountNo"),
                "tradingAccountNo": p.get("tradingAccountNo"),
                "cashAccountId": p.get("cashAccountId"),
                # also include encrypted id under alternate key for CHD
                "encryptedPortfolioId": p["id"],
                "portfolioToken": p["id"],
                "id": p["id"],
            }
            st, msg, jb = await post(body)
            print("rich", pid_name, st, msg[:180])
            log.append({"rich": pid_name, "status": st, "msg": msg, "resp": jb if st in (200, 201) else None})
            if msg.startswith("SUCCESS") or st in (200, 201):
                winning = (pid_name, body, jb)
                break

        if winning and (isinstance(winning[2], dict) and (winning[2].get("code") == 200 or winning[2].get("status") == "success" or str(winning[2]).startswith("SUCCESS"))):
            tag, body, jb = winning
            print("WINNING", tag)
            log.append({"winning": {"tag": tag, "body": {k: v for k, v in body.items() if k != "portfolioId"}, "portfolioId_kind": tag, "resp": jb}})
            if live:
                sell = dict(body)
                sell["side"] = "SELL"
                sell["securityId"] = hold["securityId"]
                sell["securityName"] = hold.get("symbol")
                sell["quantityRequested"] = 1
                spx = round(float(hold.get("marketPrice") or 1), 2)
                sell["marketPrice"] = spx
                sell["limitPrice"] = spx
                st, msg, jb = await post(sell)
                print("SELL", st, msg[:200])
                log.append({"SELL": {"status": st, "msg": msg, "resp": jb}})

    OUT.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
