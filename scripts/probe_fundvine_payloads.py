#!/usr/bin/env python3
"""Probe Fundvine ASMX with observed request shapes; dump deposit/order pages."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import httpx

BASE = "https://etrade.fundvinesecurities.com"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "fundvine_payload_probe.json"


def grab(html: str, name: str) -> str:
    m = re.search(rf'id="{name}"[^>]*value="([^"]*)"', html) or re.search(
        rf'name="{name}"[^>]*value="([^"]*)"', html
    )
    return m.group(1) if m else ""


def login(client: httpx.Client, user: str, password: str) -> None:
    r = client.get("/Login.aspx")
    tok = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]*)"', r.text)
    data = {
        "__VIEWSTATE": grab(r.text, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": grab(r.text, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": grab(r.text, "__EVENTVALIDATION"),
        "__RequestVerificationToken": tok.group(1) if tok else "",
        "txtusername": user,
        "txtpassword": password,
        "chkrememberme": "on",
        "btnsignin": "Sign In",
        "hdfshown": "login",
    }
    client.post("/Login.aspx", data=data)
    if "__NAYA.AUTH" not in client.cookies:
        raise RuntimeError("login failed")
    client.get("/Default.aspx")
    print("login ok", flush=True)


def asmx(client: httpx.Client, op: str, payload: dict) -> dict:
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
    result: dict = {}

    with httpx.Client(base_url=BASE, timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True) as client:
        login(client, user, password)
        home = client.get("/Default.aspx").text

        # CSCS options + ClientId
        client_id = None
        m = re.search(r"ClientId:\s*'(\d+)'", home) or re.search(r'ClientId:\s*"(\d+)"', home)
        if m:
            client_id = int(m.group(1))
        cscs_opts = re.findall(
            r'<option[^>]*value="([^"]+)"[^>]*>([^<]*)</option>',
            home,
            re.I,
        )
        # filter likely CSCS dropdown near drpcscsnumbers
        cscs_block = ""
        bm = re.search(
            r'id="[^"]*drpcscsnumbers[^"]*"(.*?)</select>',
            home,
            re.I | re.S,
        )
        if bm:
            cscs_block = bm.group(1)
            cscs_opts = re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', cscs_block, re.I)
        cscs_list = [v for v, _ in cscs_opts if v and v.strip()]
        result["client_id"] = client_id
        result["cscs_options"] = [{"value": v, "label": lab.strip()} for v, lab in cscs_opts]
        print("client_id", client_id, "cscs", cscs_list, flush=True)

        cscs = cscs_list[0] if cscs_list else ""
        cid = client_id or 0

        # Also try GetCSCSAccounts with empty / client id variants
        calls = {}
        for name, payload in [
            ("GetCSCSAccounts_empty", {}),
            ("GetCSCSAccounts_client", {"clientId": cid}),
            ("GetCSCSAccounts_ClientId", {"ClientId": cid}),
            ("GetCSCSAccounts_req", {"req": {"ClientId": cid}}),
        ]:
            op = "GetCSCSAccounts"
            calls[name] = asmx(client, op, payload)

        # Stock positions / dashboard / advanced portfolio
        req = {"CSCSNumber": cscs, "ClientId": cid, "Market": "", "Sector": ""}
        calls["GetStockPositions"] = asmx(client, "GetStockPositions", {"req": req})
        calls["GetDashBoardSummary"] = asmx(
            client, "GetDashBoardSummary", {"req": {"CSCSNumber": cscs, "ClientId": cid}}
        )
        calls["GetAdvancedPortfolio"] = asmx(
            client, "GetAdvancedPortfolio", {"req": {"CSCSNumber": cscs, "ClientId": cid}}
        )
        calls["GetAdvancedPortfolio2"] = asmx(
            client,
            "GetAdvancedPortfolio",
            {"CSCSNumber": cscs, "ClientId": cid},
        )

        # Statements / orders / securities
        # Inspect JS for exact shapes first via saved files if needed
        for name, op, payload in [
            ("GetAccountStatements", "GetAccountStatements", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("GetAccountStatements2", "GetAccountStatements", {"CSCSNumber": cscs, "ClientId": cid}),
            (
                "GetOrderHistoryDetails",
                "GetOrderHistoryDetails",
                {"req": {"CSCSNumber": cscs, "ClientId": cid}},
            ),
            (
                "GetOrderHistoryDetails2",
                "GetOrderHistoryDetails",
                {
                    "req": {
                        "CSCSNumber": cscs,
                        "ClientId": cid,
                        "StartDate": "",
                        "EndDate": "",
                        "Status": "",
                        "Side": "",
                    }
                },
            ),
            ("GetSecurities_empty", "GetSecurities", {}),
            ("GetSecurities_req", "GetSecurities", {"req": {}}),
            ("GetSecurities_market", "GetSecurities", {"req": {"Market": "NSE"}}),
            ("GetSecurities_market2", "GetSecurities", {"Market": "NSE"}),
            ("GetNonTradeCharges", "GetNonTradeCharges", {}),
            ("GetNonTradeCharges_req", "GetNonTradeCharges", {"req": {"ClientId": cid}}),
            ("GetDividendIntel", "GetDividendIntel", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            ("GetExitPlan", "GetExitPlan", {"req": {"CSCSNumber": cscs, "ClientId": cid}}),
            (
                "GetStatementByAccountNo",
                "GetStatementByAccountNo",
                {"req": {"AccountNo": cscs, "ClientId": cid}},
            ),
            (
                "GetContractNotesByAccountNo",
                "GetContractNotesByAccountNo",
                {"req": {"AccountNo": cscs, "ClientId": cid}},
            ),
            (
                "GetPandLStatementByAccountNo",
                "GetPandLStatementByAccountNo",
                {"req": {"AccountNo": cscs, "ClientId": cid}},
            ),
        ]:
            calls[name] = asmx(client, op, payload)

        result["calls"] = calls

        # Dump cash / deposit / new order pages
        for path, fname in [
            ("/Client/MyAccount.aspx", "fundvine_cash.html"),
            ("/Client/Deposit.aspx", "fundvine_deposit.html"),
            ("/Common/NewOrder.aspx?s=1", "fundvine_neworder_buy.html"),
            ("/Common/NewOrder.aspx?s=2", "fundvine_neworder_sell.html"),
            ("/Client/PortfolioStanding.aspx", "fundvine_portfolio.html"),
            ("/Client/MyOrders.aspx", "fundvine_orders.html"),
            ("/Client/MyTradingAccountsInfo.aspx", "fundvine_trading_accounts.html"),
            ("/Services/NaYaOMSServices.asmx", "fundvine_asmx_help.html"),
        ]:
            r = client.get(path)
            (ROOT / "logs" / fname).write_text(r.text[:250_000], encoding="utf-8", errors="replace")
            title = re.search(r"<title>([^<]+)</title>", r.text, re.I)
            print(f"saved {fname} status={r.status_code} title={title.group(1).strip() if title else '?'}", flush=True)
            # extract asmx methods from page
            methods = sorted(set(re.findall(r"NaYaOMSServices\.asmx/([A-Za-z0-9_]+)", r.text, re.I)))
            scripts = sorted(set(re.findall(r'src=["\']([^"\']+\.js[^"\']*)["\']', r.text, re.I)))
            result.setdefault("page_meta", {})[path] = {"title": title.group(1).strip() if title else None, "asmx": methods, "scripts": scripts}
            for src in scripts:
                url = src if src.startswith("/") else "/" + src.lstrip("./")
                if "nayaoms" in url.lower() or "order" in url.lower() or "deposit" in url.lower() or "account" in url.lower() or "trade" in url.lower() or "equity" in url.lower():
                    try:
                        sr = client.get(url)
                    except Exception:
                        continue
                    if sr.status_code < 400:
                        safe = url.strip("/").replace("/", "_").replace("?", "_")
                        (ROOT / "logs" / f"fundvine_js_{safe}").write_text(sr.text, encoding="utf-8", errors="replace")
                        found = sorted(set(re.findall(r"NaYaOMSServices\.asmx/([A-Za-z0-9_]+)", sr.text, re.I)))
                        print(f"  js {url} methods={found}", flush=True)

        # Parse ASMX help for all ops
        help_html = (ROOT / "logs" / "fundvine_asmx_help.html").read_text(encoding="utf-8", errors="replace")
        ops = sorted(set(re.findall(r'href="NaYaOMSServices\.asmx\?op=([^"]+)"', help_html)))
        result["help_ops"] = ops
        print("help_ops", len(ops), flush=True)
        for op in ops:
            print(" ", op, flush=True)

    # Compact call summary for stdout
    summary = {}
    for k, v in result.get("calls", {}).items():
        body = v.get("body")
        if isinstance(body, dict) and "d" in body:
            d = body["d"]
            if isinstance(d, list):
                summary[k] = {"status": v["status"], "type": "list", "n": len(d), "sample_keys": list(d[0].keys())[:20] if d and isinstance(d[0], dict) else None}
            elif isinstance(d, dict):
                summary[k] = {"status": v["status"], "type": "dict", "keys": list(d.keys())[:40]}
            else:
                summary[k] = {"status": v["status"], "type": type(d).__name__, "val": str(d)[:120]}
        else:
            summary[k] = {"status": v.get("status"), "err": str(body)[:160]}
    result["summary"] = summary
    OUT.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    print("wrote", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
