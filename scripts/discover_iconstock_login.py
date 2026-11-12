#!/usr/bin/env python3
"""Probe Icon Stock Brokers NaYa OMS login + portfolio APIs."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import httpx

BASE = "https://etrade.iconstockbrokersltd.com"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "iconstock_discover.json"


def grab(html: str, name: str) -> str:
    m = re.search(rf'id="{name}"[^>]*value="([^"]*)"', html) or re.search(
        rf'name="{name}"[^>]*value="([^"]*)"', html
    )
    return m.group(1) if m else ""


def main() -> int:
    user = os.environ.get("IC_USER", "").strip()
    password = os.environ.get("IC_PASS", "")
    if not user or not password:
        print("Set IC_USER and IC_PASS", file=sys.stderr)
        return 1

    result: dict = {}
    with httpx.Client(base_url=BASE, timeout=60.0, follow_redirects=True) as client:
        home = client.get("/")
        login = client.get("/Login.aspx")
        title = re.search(r"<title>([^<]+)</title>", login.text, re.I)
        result["login_title"] = title.group(1).strip() if title else None
        result["has_naya"] = "NaYaOMS" in login.text or "nayaoms" in login.text.lower()
        result["has_asmx_ref"] = "NaYaOMSServices" in login.text
        result["inputs"] = re.findall(r'name="([^"]+)"', login.text)[:30]

        tok = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]*)"', login.text)
        data = {
            "__VIEWSTATE": grab(login.text, "__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": grab(login.text, "__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": grab(login.text, "__EVENTVALIDATION"),
            "__RequestVerificationToken": tok.group(1) if tok else "",
            "txtusername": user,
            "txtpassword": password,
            "chkrememberme": "on",
            "btnsignin": "Sign In",
            "hdfshown": "login",
        }
        r = client.post("/Login.aspx", data=data)
        result["login_status"] = r.status_code
        result["cookies"] = list(client.cookies.keys())
        result["has_naya_auth"] = "__NAYA.AUTH" in client.cookies
        home2 = client.get("/Default.aspx")
        t2 = re.search(r"<title>([^<]+)</title>", home2.text, re.I)
        result["home_title"] = t2.group(1).strip() if t2 else None
        m = re.search(r"ClientId:\s*'(\d+)'", home2.text) or re.search(r'ClientId:\s*"(\d+)"', home2.text)
        result["client_id"] = int(m.group(1)) if m else None
        cscs_block = re.search(r'id="[^"]*drpcscsnumbers[^"]*"(.*?)</select>', home2.text, re.I | re.S)
        cscs = []
        if cscs_block:
            cscs = re.findall(r'<option[^>]*value="([^"]+)"', cscs_block.group(1), re.I)
        result["cscs"] = cscs
        print("login_auth", result["has_naya_auth"], "home", result["home_title"], "client", result["client_id"], "cscs", cscs, flush=True)

        if not result["has_naya_auth"]:
            OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
            print("login failed")
            return 1

        cid = result["client_id"] or 0
        cscs_no = cscs[0] if cscs else ""

        def asmx(op: str, payload: dict):
            resp = client.post(
                f"/Services/NaYaOMSServices.asmx/{op}",
                content=json.dumps(payload),
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            try:
                body = resp.json()
            except Exception:
                body = {"raw": resp.text[:500]}
            print(op, resp.status_code, flush=True)
            return {"status": resp.status_code, "body": body}

        calls = {}
        calls["GetCustomAppName"] = asmx("GetCustomAppName", {})
        calls["GetCSCSAccounts"] = asmx("GetCSCSAccounts", {"req": {"ClientId": cid, "CSCSNumber": ""}})
        calls["GetStockPositions"] = asmx(
            "GetStockPositions",
            {"req": {"CSCSNumber": cscs_no, "ClientId": cid, "Market": "", "Sector": ""}},
        )
        calls["GetAccountStatements"] = asmx(
            "GetAccountStatements",
            {"req": {"CSCSNumber": cscs_no, "ClientId": cid, "DateFrom": "", "DateTo": "", "Type": ""}},
        )
        calls["GetSecurities"] = asmx("GetSecurities", {"req": {}})
        calls["GetAccountAttributes"] = asmx("GetAccountAttributes", {"CSCSNumber": cscs_no})
        calls["GetPurchaseInfo"] = asmx("GetPurchaseInfo", {"req": {"AccountNumber": cscs_no}})
        result["calls"] = calls

        # menu pages
        pages = {}
        for path in [
            "/Client/Deposit.aspx",
            "/Client/EqtyMandate.aspx?s=1",
            "/Client/PortfolioStanding.aspx",
            "/Client/MyOrders.aspx",
        ]:
            pr = client.get(path)
            pt = re.search(r"<title>([^<]+)</title>", pr.text, re.I)
            pages[path] = {"status": pr.status_code, "title": pt.group(1).strip() if pt else None}
        result["pages"] = pages

    # compact summary
    summary = {}
    for k, v in result.get("calls", {}).items():
        body = v.get("body")
        if isinstance(body, dict) and "d" in body:
            d = body["d"]
            if isinstance(d, dict):
                summary[k] = {"status": v["status"], "keys": list(d.keys())[:25]}
            else:
                summary[k] = {"status": v["status"], "val": str(d)[:80]}
        else:
            summary[k] = {"status": v.get("status"), "err": str(body)[:120]}
    result["summary"] = summary
    OUT.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
