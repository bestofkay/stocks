#!/usr/bin/env python3
"""Compare list vs detail portfolio ids; try create with detail id + matching secure token."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.connectors.investnaija import api_paths as ap  # noqa: E402

OUT = ROOT / "logs" / "investnaija_order_probe15.json"


def encrypt_password(pw: str) -> str:
    der = base64.b64decode(ap.PASSWORD_PUBLIC_KEY_B64)
    key = serialization.load_der_public_key(der)
    ct = key.encrypt(
        pw.encode("utf-8"),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return base64.b64encode(ct).decode("ascii")


def classify(jb) -> str:
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
    log = []
    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        login = (await client.post(ap.LOGIN, json={"email": email, "password": encrypt_password(password), "rememberMe": True})).json()
        auth = {**headers, "Authorization": login["token"], "x-uuid-token": login["uuid_token"]}
        p = (await client.get(ap.TRADE_PORTFOLIOS, headers=auth)).json()["data"][0]
        list_id, list_sig = p["id"], p["signature"]
        # fetch detail with list sig
        detail_resp = (await client.get(ap.trade_portfolio(list_id), headers={**auth, "x-secure-token": list_sig})).json()
        detail = detail_resp.get("data") or {}
        detail_pid = detail.get("portfolioId")
        # Does detail include its own signature?
        print("list_id len", len(list_id), "sig len", len(list_sig))
        print("detail_pid len", len(detail_pid) if detail_pid else None)
        print("detail keys sample", sorted(detail.keys())[:40])
        print("detail has signature", "signature" in detail)
        # Re-fetch list to see if signature rotates; also try GET detail using detail_pid as path
        if detail_pid:
            for path_id, sig, label in [
                (list_id, list_sig, "list+listsig"),
                (detail_pid, list_sig, "detail+listsig"),
                (list_id, detail.get("signature") or list_sig, "list+detailsig"),
                (detail_pid, detail.get("signature") or list_sig, "detail+detailsig"),
            ]:
                rr = await client.get(ap.trade_portfolio(path_id), headers={**auth, "x-secure-token": sig})
                print("GET detail", label, rr.status_code, str(rr.json())[:100])

        day_term = next(t for t in (await client.get(ap.ORDER_TERMS, headers=auth)).json()["data"]["result"] if "DAY" in t["label"].upper())
        buy = next(s for s in (await client.get(ap.SECURITIES, headers=auth)).json()["data"] if s.get("symbol") == "JAIZBANK")
        hold = p["portfolioHoldings"][0]
        px = round(float(buy["price"]), 2)

        def body(pid):
            return {
                "portfolioId": pid,
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

        trials = []
        # Create with various id/sig combos
        for pid, sig, label in [
            (list_id, list_sig, "list_id+list_sig"),
            (detail_pid, list_sig, "detail_pid+list_sig"),
            (detail_pid, detail.get("signature") or list_sig, "detail_pid+detail_sig"),
            (list_id, detail.get("signature") or list_sig, "list_id+detail_sig"),
            # Truncate list_id? maybe too long for Java field
            (list_id[:36], list_sig, "list_id_trunc36"),
            (list_id[:64], list_sig, "list_id_trunc64"),
        ]:
            if not pid:
                continue
            rr = await client.post(ap.CREATE_ORDER, headers={**auth, "x-secure-token": sig}, json=body(pid))
            jb = rr.json()
            msg = classify(jb)
            print("CREATE", label, "pidlen", len(pid), rr.status_code, msg[:160])
            trials.append({"label": label, "pid_len": len(pid), "status": rr.status_code, "msg": msg, "resp": jb})
            if msg.startswith("SUCCESS") or rr.status_code in (200, 201):
                log.append({"winning": {"label": label, "body": body(pid), "resp": jb}})
                print("WINNING", label)
                if live:
                    sell = body(pid)
                    sell["side"] = "SELL"
                    sell["securityId"] = hold["securityId"]
                    sell["securityName"] = hold.get("symbol")
                    spx = round(float(hold.get("marketPrice") or 1), 2)
                    sell["marketPrice"] = spx
                    sell["limitPrice"] = spx
                    rr2 = await client.post(ap.CREATE_ORDER, headers={**auth, "x-secure-token": sig}, json=sell)
                    print("SELL", rr2.status_code, classify(rr2.json())[:200])
                    log.append({"SELL": rr2.json()})
                break

        # Character analysis of list_id vs detail_pid
        log.append({
            "list_id": list_id,
            "detail_pid": detail_pid,
            "list_sig_len": len(list_sig),
            "list_id_len": len(list_id),
            "detail_pid_len": len(detail_pid) if detail_pid else None,
            "list_chars": sorted(set(list_id)),
            "detail_chars": sorted(set(detail_pid)) if detail_pid else None,
            "trials": trials,
        })

        # Maybe create wants BOTH ids
        if detail_pid:
            for combo_label, b in [
                ("both_ids", {**body(list_id), "portfolioToken": detail_pid}),
                ("swap", {**body(detail_pid), "id": list_id}),
                ("account_plus_list", {**body(list_id), "accountNo": p.get("accountNo"), "cashAccountId": p.get("cashAccountId")}),
            ]:
                rr = await client.post(ap.CREATE_ORDER, headers={**auth, "x-secure-token": list_sig}, json=b)
                msg = classify(rr.json())
                print("combo", combo_label, rr.status_code, msg[:160])
                trials.append({"label": combo_label, "status": rr.status_code, "msg": msg})
                if msg.startswith("SUCCESS"):
                    log.append({"winning": b, "resp": rr.json()})
                    break

    OUT.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
