#!/usr/bin/env python3
"""Probe Fundvine order pages, GetAccountAttributes, deposit charges, SignalR hubs."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx

BASE = "https://etrade.fundvinesecurities.com"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "fundvine_orders_deposit_probe.json"


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
    client.get("/Default.aspx")


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

        result["GetAccountAttributes"] = asmx(client, "GetAccountAttributes", {"CSCSNumber": cscs})
        result["GetNonTradeCharges"] = asmx(
            client, "GetNonTradeCharges", {"transtype": 3, "instrument": "DEBIT/CREDIT CARD"}
        )

        # Fetch order mandate pages + extract scripts/methods
        for path, name in [
            ("/Common/NewOrder.aspx?s=1&sym=GTCO", "neworder_gtco"),
            ("/Client/EqtyMandate.aspx?s=1", "eqty_mandate_buy"),
            ("/Client/EqtyMandate.aspx?s=2", "eqty_mandate_sell"),
            ("/Client/MyOrders.aspx", "myorders"),
            ("/signalr/signalr/hubs", "signalr_hubs"),
            ("/signalr/hubs", "signalr_hubs2"),
        ]:
            r = client.get(path)
            fname = ROOT / "logs" / f"fundvine_{name}.html"
            fname.write_text(r.text[:300_000], encoding="utf-8", errors="replace")
            title = re.search(r"<title>([^<]+)</title>", r.text, re.I)
            methods = sorted(set(re.findall(r"NaYaOMSServices\.asmx/([A-Za-z0-9_]+)", r.text, re.I)))
            scripts = sorted(set(re.findall(r'src=["\']([^"\']+\.js[^"\']*)["\']', r.text, re.I)))
            print(f"{path} -> {r.status_code} {title.group(1).strip() if title else '?'} asmx={methods}", flush=True)
            result.setdefault("pages", {})[path] = {
                "status": r.status_code,
                "title": title.group(1).strip() if title else None,
                "asmx": methods,
                "scripts": scripts,
                "len": len(r.text),
            }
            for src in scripts:
                url = src if src.startswith("/") else "/" + src.lstrip("./")
                if any(k in url.lower() for k in ("order", "mandate", "eqty", "trade", "nayaoms_new", "fix")):
                    try:
                        sr = client.get(url)
                    except Exception:
                        continue
                    if sr.status_code < 400 and len(sr.text) < 2_000_000:
                        safe = url.strip("/").replace("/", "_").replace("?", "_")
                        (ROOT / "logs" / f"fundvine_js_{safe}").write_text(sr.text, encoding="utf-8", errors="replace")
                        found = sorted(set(re.findall(r"NaYaOMSServices\.asmx/([A-Za-z0-9_]+)", sr.text, re.I)))
                        hubs = sorted(set(re.findall(r"\.server\.([A-Za-z0-9_]+)", sr.text)))
                        print(f"  {url} asmx={found} hub={hubs[:30]}", flush=True)

        # Probe likely order-list methods from help/name guesses using observed req shapes
        for op, payload in [
            ("GetMyOrders", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("GetOrderHistory", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("GetOrdersByAccount", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("GetTradeActivity", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("GetClientOrders", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("GetPendingOrders", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("GetAllOrders", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("GetOrderBook", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("GetTodaysOrders", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("GetExecutedOrders", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("SendOrder", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("PlaceOrder", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("SubmitOrder", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("NewOrder", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("CreateOrder", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("PostNewOrder", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("SendNewOrder", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("SubmitNewOrder", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("SendEquityOrder", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("ProcessOrder", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("GetCashPositions", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("GetCashPosition", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("GetAccountBalances", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("GetFundingAccounts", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("GetBankAccounts", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("GetDepositInstructions", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
        ]:
            result.setdefault("guesses", {})[op] = asmx(client, op, payload)

        # Parse signalr hubs file for method names
        for fname in ["fundvine_signalr_hubs.html", "fundvine_signalr_hubs2.html"]:
            p = ROOT / "logs" / fname
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            # extract hub method names
            methods = sorted(set(re.findall(r"""['"]([A-Za-z][A-Za-z0-9_]{2,})['"]\s*:""", text)))
            result.setdefault("signalr", {})[fname] = {
                "len": len(text),
                "snip": text[:1500],
                "keys_sample": methods[:80],
            }
            print(f"signalr {fname} len={len(text)}", flush=True)

        # Scan NewOrder HTML for form field names
        no = (ROOT / "logs" / "fundvine_neworder_gtco.html").read_text(encoding="utf-8", errors="replace")
        inputs = re.findall(r"<(?:input|select|textarea)[^>]+>", no, re.I)
        result["neworder_fields"] = []
        for inp in inputs:
            nm = re.search(r'name="([^"]+)"', inp, re.I)
            iid = re.search(r'id="([^"]+)"', inp, re.I)
            if nm or iid:
                result["neworder_fields"].append({"name": nm.group(1) if nm else None, "id": iid.group(1) if iid else None, "tag": inp[:200]})

        # Extract inline script blocks mentioning Send/Order
        for m in re.finditer(r"<script[^>]*>(.*?)</script>", no, re.I | re.S):
            block = m.group(1)
            if any(k in block for k in ("SendOrder", "PlaceOrder", "Submit", "asmx", "eTradeHub", ".server.")):
                snip = re.sub(r"\s+", " ", block)[:800]
                result.setdefault("neworder_scripts", []).append(snip)

    OUT.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print("wrote", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
