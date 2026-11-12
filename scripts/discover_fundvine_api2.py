#!/usr/bin/env python3
"""Faster Fundvine discovery: login, home menu, WSDL ops, sample ASMX calls."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import httpx

BASE = "https://etrade.fundvinesecurities.com"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "fundvine_api_discover2.json"


def grab(html: str, name: str) -> str:
    m = re.search(rf'id="{name}"[^>]*value="([^"]*)"', html) or re.search(
        rf'name="{name}"[^>]*value="([^"]*)"', html
    )
    return m.group(1) if m else ""


def login(client: httpx.Client, user: str, password: str) -> None:
    r = client.get("/Login.aspx")
    data = {
        "__VIEWSTATE": grab(r.text, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": grab(r.text, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": grab(r.text, "__EVENTVALIDATION"),
        "__RequestVerificationToken": grab(r.text, "__REQUESTVERIFICATIONTOKEN")
        or grab(r.text, "__RequestVerificationToken"),
        "txtusername": user,
        "txtpassword": password,
        "chkrememberme": "on",
        "btnsignin": "Sign In",
        "hdfshown": "login",
    }
    # token cookie name vs form field — re-read from HTML properly
    m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]*)"', r.text)
    if m:
        data["__RequestVerificationToken"] = m.group(1)
    r2 = client.post("/Login.aspx", data=data)
    if "__NAYA.AUTH" not in client.cookies:
        raise RuntimeError(f"login failed status={r2.status_code}")
    # land on default
    client.get("/Default.aspx")
    print("login ok", flush=True)


def main() -> int:
    user = os.environ["FV_USER"].strip()
    password = os.environ["FV_PASS"]
    result: dict = {}

    timeout = httpx.Timeout(20.0, connect=10.0)
    with httpx.Client(base_url=BASE, timeout=timeout, follow_redirects=True) as client:
        login(client, user, password)

        home = client.get("/Default.aspx")
        print(f"home {home.status_code} len={len(home.text)}", flush=True)
        (ROOT / "logs" / "fundvine_home.html").write_text(home.text, encoding="utf-8", errors="replace")
        title = re.search(r"<title>([^<]+)</title>", home.text, re.I)
        result["home_title"] = title.group(1).strip() if title else None

        hrefs = sorted(set(re.findall(r'href=["\']([^"\'#]+)["\']', home.text, re.I)))
        aspx = [h for h in hrefs if ".aspx" in h.lower()]
        result["menu_aspx"] = aspx
        scripts = sorted(set(re.findall(r'src=["\']([^"\']+\.js[^"\']*)["\']', home.text, re.I)))
        result["scripts"] = scripts
        print(f"menu pages={len(aspx)} scripts={len(scripts)}", flush=True)

        # WSDL
        wsdl = client.get("/Services/NaYaOMSServices.asmx?WSDL")
        print(f"wsdl {wsdl.status_code} len={len(wsdl.text)}", flush=True)
        (ROOT / "logs" / "fundvine_wsdl.xml").write_text(wsdl.text, encoding="utf-8", errors="replace")
        ops = sorted(set(re.findall(r'<wsdl:operation name="([^"]+)"', wsdl.text)))
        if not ops:
            ops = sorted(set(re.findall(r'<operation name="([^"]+)"', wsdl.text)))
        result["wsdl_operations"] = ops
        print(f"ops={len(ops)}", flush=True)
        for op in ops:
            print(" ", op, flush=True)

        # ASMX help page method list (HTML)
        help_page = client.get("/Services/NaYaOMSServices.asmx")
        (ROOT / "logs" / "fundvine_asmx_help.html").write_text(help_page.text, encoding="utf-8", errors="replace")
        help_ops = sorted(set(re.findall(r'href="NaYaOMSServices\.asmx\?op=([^"]+)"', help_page.text)))
        result["help_operations"] = help_ops

        # Scan scripts for method usage + payloads
        methods_used: set[str] = set()
        ajax_samples: list[dict] = []
        for src in scripts:
            url = src if src.startswith("/") else "/" + src.lstrip("./")
            if url.startswith("http"):
                continue
            try:
                sr = client.get(url)
            except Exception as exc:
                print("script fail", url, exc, flush=True)
                continue
            if sr.status_code >= 400:
                continue
            for m in re.finditer(r"NaYaOMSServices\.asmx/([A-Za-z0-9_]+)", sr.text, re.I):
                methods_used.add(m.group(1))
            # save scripts that mention asmx
            if "asmx" in sr.text.lower() or "NaYaOMS" in sr.text:
                safe = url.strip("/").replace("/", "_")
                (ROOT / "logs" / f"fundvine_js_{safe}").write_text(sr.text, encoding="utf-8", errors="replace")
                for m in re.finditer(
                    r"\$\.ajax\(\s*\{.{0,500}?NaYaOMSServices\.asmx/[A-Za-z0-9_]+.{0,300}?\}",
                    sr.text,
                    re.S,
                ):
                    ajax_samples.append({"src": url, "snip": re.sub(r"\s+", " ", m.group(0))[:500]})
        result["methods_used"] = sorted(methods_used)
        result["ajax_samples"] = ajax_samples[:40]
        print("methods_used", sorted(methods_used), flush=True)

        # Visit menu pages quickly
        pages = {}
        for h in aspx:
            path = h
            if path.startswith("http"):
                if "fundvinesecurities.com" not in path:
                    continue
                path = "/" + path.split(".com/", 1)[-1]
            if not path.startswith("/"):
                path = "/" + path
            path = path.split("?")[0]
            try:
                r = client.get(path)
            except Exception as exc:
                pages[path] = {"error": str(exc)}
                continue
            t = re.search(r"<title>([^<]+)</title>", r.text, re.I)
            title_s = t.group(1).strip() if t else None
            page_methods = sorted(set(re.findall(r"NaYaOMSServices\.asmx/([A-Za-z0-9_]+)", r.text, re.I)))
            page_scripts = sorted(set(re.findall(r'src=["\']([^"\']+\.js[^"\']*)["\']', r.text, re.I)))
            pages[path] = {
                "status": r.status_code,
                "title": title_s,
                "asmx": page_methods,
                "scripts": page_scripts[:20],
                "len": len(r.text),
            }
            print(f"page {path} -> {r.status_code} {title_s} asmx={page_methods}", flush=True)
            if r.status_code == 200 and title_s and "Login" not in title_s:
                safe = path.strip("/").replace("/", "_") or "root"
                (ROOT / "logs" / f"fundvine_page_{safe}.html").write_text(
                    r.text[:150_000], encoding="utf-8", errors="replace"
                )
                # harvest script methods
                for src in page_scripts:
                    url = src if src.startswith("/") else "/" + src.lstrip("./")
                    if url.startswith("http"):
                        continue
                    try:
                        sr = client.get(url)
                    except Exception:
                        continue
                    if sr.status_code >= 400:
                        continue
                    found = set(re.findall(r"NaYaOMSServices\.asmx/([A-Za-z0-9_]+)", sr.text, re.I))
                    if found:
                        methods_used |= found
                        safe = url.strip("/").replace("/", "_")
                        (ROOT / "logs" / f"fundvine_js_{safe}").write_text(
                            sr.text, encoding="utf-8", errors="replace"
                        )
                        print(f"  script {url} methods={sorted(found)}", flush=True)

        result["pages"] = pages
        result["methods_used"] = sorted(methods_used)

        # Probe a few likely no-arg ASMX JSON methods
        headers = {"Content-Type": "application/json; charset=utf-8"}
        probe_ops = list(
            dict.fromkeys(
                list(methods_used)
                + [
                    "GetCustomAppName",
                    "GetInvestorProfile",
                    "GetClientProfile",
                    "GetPortfolio",
                    "GetStockPortfolio",
                    "GetEquityPortfolio",
                    "GetCashBalance",
                    "GetCashAccounts",
                    "GetAccounts",
                    "GetHoldings",
                    "GetOrders",
                    "GetOpenOrders",
                    "GetMarketWatch",
                    "GetSecurities",
                    "GetDepositAccounts",
                    "GetVirtualAccounts",
                    "GetBanks",
                    "GetAccountSummary",
                ]
            )
        )
        calls = {}
        for op in probe_ops[:80]:
            try:
                r = client.post(f"/Services/NaYaOMSServices.asmx/{op}", content="{}", headers=headers)
                body = r.text[:1500]
                calls[op] = {"status": r.status_code, "body": body}
                print(f"asmx {op} -> {r.status_code} {body[:120].replace(chr(10),' ')}", flush=True)
            except Exception as exc:
                calls[op] = {"error": str(exc)}
                print(f"asmx {op} ERR {exc}", flush=True)
        result["asmx_probes"] = calls

    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("wrote", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
