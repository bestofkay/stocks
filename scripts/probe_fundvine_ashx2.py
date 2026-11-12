#!/usr/bin/env python3
"""Retry OrderHistoryJSON.ashx with MyOrders referer + DataTables-shaped body."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx

BASE = "https://etrade.fundvinesecurities.com"
OUT = Path(__file__).resolve().parents[1] / "logs" / "fundvine_ashx2.json"


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
    client.get("/Client/MyOrders.aspx")


def main() -> int:
    user = os.environ["FV_USER"].strip()
    password = os.environ["FV_PASS"]
    result = {}
    with httpx.Client(base_url=BASE, timeout=30.0, follow_redirects=True) as client:
        login(client, user, password)
        headers = {
            "Referer": f"{BASE}/Client/MyOrders.aspx",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        # mimic jquery datatables form encoding
        form = [
            ("draw", "1"),
            ("start", "0"),
            ("length", "10"),
            ("sCSCSNumber", "0138054839"),
            ("sClientId", "2916"),
            ("sSideFilter", ""),
            ("sStatusFilter", ""),
            ("sStartTimeFilter", ""),
            ("sEndTimeFilter", ""),
            ("sSecurityId", ""),
        ]
        for path in ["/Handlers/OrderHistoryJSON.ashx", "/Handlers/ClientOrdersJSON.ashx"]:
            r = client.post(path, data=form, headers=headers)
            ctype = r.headers.get("content-type")
            print(path, r.status_code, ctype, r.text[:200].replace("\n", " "), flush=True)
            try:
                body = r.json()
            except Exception:
                body = {"raw": r.text[:2000]}
            result[path] = {"status": r.status_code, "ctype": ctype, "body": body}

        # Dashboard summary already has order counts
        r = client.post(
            "/Services/NaYaOMSServices.asmx/GetDashBoardSummary",
            content=json.dumps({"req": {"CSCSNumber": "0138054839", "ClientId": 2916}}),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        result["dashboard"] = r.json()
        print("dashboard", str(result["dashboard"])[:400], flush=True)

    OUT.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
