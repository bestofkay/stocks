#!/usr/bin/env python3
"""Probe OrderHistoryJSON.ashx with observed DataTables fields."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx

BASE = "https://etrade.fundvinesecurities.com"
OUT = Path(__file__).resolve().parents[1] / "logs" / "fundvine_orderhistory_ashx.json"


def grab(html: str, name: str) -> str:
    m = re.search(rf'id="{name}"[^>]*value="([^"]*)"', html) or re.search(
        rf'name="{name}"[^>]*value="([^"]*)"', html
    )
    return m.group(1) if m else ""


def login(client: httpx.Client, user: str, password: str) -> None:
    r = client.get("/Login.aspx")
    tok = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]*)"', r.text)
    client.post(
        "/Login.aspx",
        data={
            "__VIEWSTATE": grab(r.text, "__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": grab(r.text, "__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": grab(r.text, "__EVENTVALIDATION"),
            "__RequestVerificationToken": tok.group(1) if tok else "",
            "txtusername": user,
            "txtpassword": password,
            "chkrememberme": "on",
            "btnsignin": "Sign In",
            "hdfshown": "login",
        },
    )
    assert "__NAYA.AUTH" in client.cookies


def asmx(client: httpx.Client, op: str, payload: dict):
    r = client.post(
        f"/Services/NaYaOMSServices.asmx/{op}",
        content=json.dumps(payload),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:2000]}
    return {"status": r.status_code, "body": body}


def main() -> int:
    user = os.environ["FV_USER"].strip()
    password = os.environ["FV_PASS"]
    cscs = "0138054839"
    cid = 2916
    result = {}

    with httpx.Client(base_url=BASE, timeout=30.0, follow_redirects=True) as client:
        login(client, user, password)

        for path in ["/Handlers/OrderHistoryJSON.ashx", "/Handlers/ClientOrdersJSON.ashx"]:
            data = {
                "draw": "1",
                "start": "0",
                "length": "50",
                "sCSCSNumber": cscs,
                "sClientId": str(cid),
                "sSideFilter": "",
                "sStatusFilter": "",
                "sStartTimeFilter": "",
                "sEndTimeFilter": "",
                "sSecurityId": "",
            }
            r = client.post(path, data=data)
            text = r.text[:3000]
            try:
                body = r.json()
            except Exception:
                body = {"raw": text}
            result[path] = {"status": r.status_code, "ctype": r.headers.get("content-type"), "body": body}
            print(path, r.status_code, r.headers.get("content-type"), str(body)[:300], flush=True)

        inv = asmx(
            client,
            "IsMandateValid",
            {
                "req": {
                    "AccountNumber": cscs,
                    "CSCSNumber": "",
                    "SecurityId": "GTCO",
                    "Quantity": "1",
                    "IsLimit": True,
                    "Price": "70",
                    "Yield": "",
                    "IsBuy": True,
                    "ClientId": cid,
                }
            },
        )
        result["IsMandateValid"] = inv
        print("IsMandateValid", inv["status"], str(inv["body"])[:400], flush=True)

        pi = asmx(client, "GetPurchaseInfo", {"req": {"AccountNumber": cscs}})
        result["GetPurchaseInfo"] = pi
        print("GetPurchaseInfo", pi["status"], str(pi["body"])[:500], flush=True)

        # cookies for session persistence shape
        result["cookies"] = {k: (v[:20] + "...") for k, v in client.cookies.items()}

    OUT.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
