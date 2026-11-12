#!/usr/bin/env python3
"""Probe ClientOrdersJSON.ashx + mandate preview APIs."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlencode

import httpx

BASE = "https://etrade.fundvinesecurities.com"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "fundvine_orders_ashx.json"


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
    print(f"{op} -> {r.status_code}", flush=True)
    return {"status": r.status_code, "body": body}


def main() -> int:
    user = os.environ["FV_USER"].strip()
    password = os.environ["FV_PASS"]
    cscs = "0138054839"
    cid = 2916
    result: dict = {}

    with httpx.Client(base_url=BASE, timeout=30.0, follow_redirects=True) as client:
        login(client, user, password)

        # DataTables order history
        for status in ["", "All", "Pending", "Filled", "COMPLETE", "Settled", "Open"]:
            data = {
                "draw": "1",
                "start": "0",
                "length": "50",
                "sCSCSNumber": cscs,
                "sSideFilter": "",
                "sStartTimeFilter": "",
                "sEndTimeFilter": "",
                "sSecurityId": "",
                "sStatusFilter": status,
            }
            # Also try status field names from JS
            r = client.post("/Handlers/ClientOrdersJSON.ashx", data=data)
            try:
                body = r.json()
            except Exception:
                body = {"raw": r.text[:1500]}
            key = f"orders_status_{status or 'blank'}"
            result[key] = {"http": r.status_code, "body": body}
            print(f"ashx status={status!r} -> {r.status_code} keys={list(body)[:10] if isinstance(body, dict) else type(body)}", flush=True)

        # Read more of orderhistory for non-settled ajax data fields
        js = (ROOT / "logs" / "fundvine_js_Resources_js_nayaoms_orderhistory.js").read_text(encoding="utf-8", errors="replace")
        m = re.search(r'else \{.*?ajax:\s*\{.*?"url":\s*"([^"]+)".*?"data":\s*function\s*\(d\)\s*\{(.*?)\}', js, re.S)
        if m:
            result["ashx_url"] = m.group(1)
            result["ashx_data_fn"] = re.sub(r"\s+", " ", m.group(2))[:800]
            print("ashx data fn", result["ashx_data_fn"], flush=True)

        # Purchase / sale info + mandate valid
        result["GetPurchaseInfo"] = asmx(client, "GetPurchaseInfo", {"req": {"AccountNumber": cscs}})
        result["GetSaleInfo"] = asmx(
            client, "GetSaleInfo", {"req": {"AccountNumber": cscs, "SecurityId": "GTCO"}}
        )
        result["GetBidsOffersQty"] = asmx(
            client, "GetBidsOffersQty", {"SecurityID": "GTCO", "Market": "EQTY"}
        )
        # try variants from page
        result["GetBidsOffersQty2"] = asmx(
            client, "GetBidsOffersQty", {"req": {"SecurityID": "GTCO"}}
        )

        # IsMandateValid - read payload from HTML
        html = (ROOT / "logs" / "fundvine_eqty_mandate_buy.html").read_text(encoding="utf-8", errors="replace")
        block = re.search(r"IsMandateValid.*?data:\s*JSON\.stringify\(\{(.*?)\}\)", html, re.S)
        if block:
            result["IsMandateValid_snip"] = re.sub(r"\s+", " ", block.group(0))[:1000]
            print(result["IsMandateValid_snip"], flush=True)

        result["IsMandateValid"] = asmx(
            client,
            "IsMandateValid",
            {
                "req": {
                    "AccountNumber": cscs,
                    "SecurityID": "GTCO",
                    "Side": "1",
                    "Quantity": 1,
                    "Price": 1,
                    "OrderType": "LIMIT",
                    "Market": "EQTY",
                }
            },
        )

        # GetAllOrders with flags?
        result["GetAllOrders_flags"] = asmx(
            client,
            "GetAllOrders",
            {
                "req": {
                    "CSCSNumber": cscs,
                    "ClientId": cid,
                    "IncludeQueuedOrders": True,
                    "IncludeSentOrders": True,
                    "IncludePendingOrders": True,
                    "IncludeOrderStatuses": True,
                    "IncludeTradedOrders": True,
                    "IncludeRejectedOrders": True,
                    "IncludeCancelledOrders": True,
                }
            },
        )
        result["GetAllOrders_empty"] = asmx(client, "GetAllOrders", {})

    OUT.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print("wrote", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
