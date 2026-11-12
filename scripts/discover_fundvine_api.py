#!/usr/bin/env python3
"""Authenticated discovery of Fundvine NaYa OMS pages + ASMX methods."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import httpx

BASE = "https://etrade.fundvinesecurities.com"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "fundvine_api_discover.json"


def login(client: httpx.Client, user: str, password: str) -> None:
    r = client.get("/Login.aspx")
    html = r.text

    def grab(name: str) -> str:
        m = re.search(rf'id="{name}"[^>]*value="([^"]*)"', html) or re.search(
            rf'name="{name}"[^>]*value="([^"]*)"', html
        )
        return m.group(1) if m else ""

    data = {
        "__VIEWSTATE": grab("__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": grab("__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": grab("__EVENTVALIDATION"),
        "__RequestVerificationToken": grab("__RequestVerificationToken"),
        "txtusername": user,
        "txtpassword": password,
        "chkrememberme": "on",
        "btnsignin": "Sign In",
        "hdfshown": "login",
    }
    r2 = client.post("/Login.aspx", data=data, follow_redirects=False)
    if r2.status_code not in (302, 301) or "__NAYA.AUTH" not in client.cookies:
        raise RuntimeError(f"login failed: {r2.status_code} cookies={list(client.cookies.keys())}")
    client.get(r2.headers["location"], follow_redirects=True)


def main() -> int:
    user = os.environ.get("FV_USER", "").strip()
    password = os.environ.get("FV_PASS", "")
    if not user or not password:
        print("Set FV_USER and FV_PASS", file=sys.stderr)
        return 1

    result: dict = {"pages": {}, "scripts": [], "asmx": {}, "ajax_calls": [], "menu": []}

    with httpx.Client(base_url=BASE, timeout=90.0, follow_redirects=False) as client:
        login(client, user, password)

        # Home / Default
        home = client.get("/Default.aspx", follow_redirects=True)
        result["home_status"] = home.status_code
        result["home_url"] = str(home.url)
        title = re.search(r"<title>([^<]+)</title>", home.text, re.I)
        result["home_title"] = title.group(1).strip() if title else None
        (ROOT / "logs" / "fundvine_home.html").write_text(home.text, encoding="utf-8", errors="replace")

        # Collect menu/nav links
        hrefs = sorted(set(re.findall(r'href=["\']([^"\']+)["\']', home.text, re.I)))
        aspx = [h for h in hrefs if ".aspx" in h.lower()]
        result["menu"] = aspx
        scripts = sorted(set(re.findall(r'src=["\']([^"\']+\.js[^"\']*)["\']', home.text, re.I)))
        result["scripts"] = scripts

        # Fetch ASMX WSDL / help
        for path in [
            "/Services/NaYaOMSServices.asmx",
            "/Services/NaYaOMSServices.asmx?WSDL",
            "/Services/NaYaOMSServices.asmx?disco",
        ]:
            r = client.get(path, follow_redirects=True)
            result["asmx"][path] = {
                "status": r.status_code,
                "content_type": r.headers.get("content-type"),
                "len": len(r.content),
                "snip": r.text[:2000],
            }
            safe = path.strip("/").replace("/", "_").replace("?", "_")
            (ROOT / "logs" / f"fundvine_{safe}.txt").write_text(r.text, encoding="utf-8", errors="replace")

        # Scan all scripts referenced from home for ASMX method names
        method_pat = re.compile(
            r"""NaYaOMSServices\.asmx/([A-Za-z0-9_]+)|['"]([A-Za-z][A-Za-z0-9_]{3,})['"]\s*,""",
            re.I,
        )
        asmx_methods: set[str] = set()
        ajax_snips: list[str] = []
        for src in scripts:
            url = src if src.startswith("http") else ("/" + src.lstrip("./"))
            try:
                sr = client.get(url, follow_redirects=True)
            except Exception as exc:
                result["pages"][url] = f"err {exc}"
                continue
            if sr.status_code >= 400:
                continue
            text = sr.text
            for m in re.finditer(r"""NaYaOMSServices\.asmx/([A-Za-z0-9_]+)""", text, re.I):
                asmx_methods.add(m.group(1))
            for m in re.finditer(r"""(?:url|URL)\s*[:=]\s*['"]([^'"]+)['"]""", text):
                if "asmx" in m.group(1).lower() or "service" in m.group(1).lower():
                    ajax_snips.append(m.group(1))
            for m in re.finditer(r"""\$\.ajax\(\s*\{[^}]{0,400}\}""", text, re.S):
                snip = re.sub(r"\s+", " ", m.group(0))[:300]
                if "asmx" in snip.lower() or "Service" in snip:
                    ajax_snips.append(snip)

        result["asmx_methods_from_js"] = sorted(asmx_methods)
        result["ajax_calls"] = ajax_snips[:100]

        # Visit interesting aspx pages (first wave from menu + guesses)
        guesses = [
            "/Default.aspx",
            "/PortfolioSummary.aspx",
            "/StockPortfolio.aspx",
            "/EquityPortfolio.aspx",
            "/CashBalance.aspx",
            "/CashAccounts.aspx",
            "/OrderBook.aspx",
            "/TradeOrder.aspx",
            "/PlaceOrder.aspx",
            "/BuyOrder.aspx",
            "/SellOrder.aspx",
            "/MarketWatch.aspx",
            "/Securities.aspx",
            "/AccountSummary.aspx",
            "/TransactionHistory.aspx",
            "/Statements.aspx",
            "/Deposit.aspx",
            "/FundAccount.aspx",
            "/VirtualAccount.aspx",
            "/Profile.aspx",
            "/InvestorProfile.aspx",
            "/ClientProfile.aspx",
            "/ChangePassword.aspx",
        ]
        # normalize menu relative links
        pages = []
        for h in aspx + guesses:
            if h.startswith("http"):
                if "fundvinesecurities.com" not in h:
                    continue
                h = "/" + h.split(".com/", 1)[-1]
            if not h.startswith("/"):
                h = "/" + h
            pages.append(h.split("#")[0])
        pages = list(dict.fromkeys(pages))

        for p in pages[:60]:
            r = client.get(p, follow_redirects=True)
            info: dict = {
                "status": r.status_code,
                "url": str(r.url),
                "len": len(r.content),
            }
            t = re.search(r"<title>([^<]+)</title>", r.text, re.I)
            info["title"] = t.group(1).strip() if t else None
            # extract script srcs and asmx refs from page
            info["scripts"] = sorted(set(re.findall(r'src=["\']([^"\']+\.js[^"\']*)["\']', r.text, re.I)))[:30]
            info["asmx"] = sorted(set(re.findall(r"""NaYaOMSServices\.asmx/([A-Za-z0-9_]+)""", r.text, re.I)))
            info["inline_ajax"] = [
                re.sub(r"\s+", " ", m.group(0))[:250]
                for m in re.finditer(r"""(?:url|URL)\s*[:=]\s*['"]([^'"]*asmx[^'"]*)['"]""", r.text, re.I)
            ][:20]
            # Also capture PageMethods
            info["pagemethods"] = sorted(set(re.findall(r"PageMethods\.([A-Za-z0-9_]+)", r.text)))
            result["pages"][p] = info
            if r.status_code == 200 and info["title"] and "Login" not in info["title"] and "404" not in (
                info["title"] or ""
            ):
                safe = p.strip("/").replace("/", "_") or "root"
                (ROOT / "logs" / f"fundvine_page_{safe}.html").write_text(
                    r.text[:200_000], encoding="utf-8", errors="replace"
                )

            # harvest more methods from page-specific scripts
            for src in info["scripts"]:
                url = src if src.startswith("/") else "/" + src.lstrip("./")
                if url.startswith("http"):
                    continue
                try:
                    sr = client.get(url, follow_redirects=True)
                except Exception:
                    continue
                if sr.status_code >= 400:
                    continue
                for m in re.finditer(r"""NaYaOMSServices\.asmx/([A-Za-z0-9_]+)""", sr.text, re.I):
                    asmx_methods.add(m.group(1))

        result["asmx_methods_from_js"] = sorted(asmx_methods)

        # If WSDL listed methods, parse them
        wsdl = result["asmx"].get("/Services/NaYaOMSServices.asmx?WSDL", {}).get("snip", "")
        wsdl_full = (ROOT / "logs" / "fundvine_Services_NaYaOMSServices.asmx_WSDL.txt")
        if wsdl_full.exists():
            text = wsdl_full.read_text(encoding="utf-8", errors="replace")
            ops = sorted(set(re.findall(r'<wsdl:operation name="([^"]+)"', text)))
            if not ops:
                ops = sorted(set(re.findall(r'<operation name="([^"]+)"', text)))
            result["wsdl_operations"] = ops

    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("home_title", "menu", "asmx_methods_from_js", "wsdl_operations") if k in result}, indent=2))
    print("pages_ok:")
    for p, info in result["pages"].items():
        if info.get("status") == 200 and info.get("title") and "Login" not in info["title"] and "Error" not in (
            info["title"] or ""
        ):
            print(f"  {p} -> {info['title']} asmx={info.get('asmx')} pm={info.get('pagemethods')}")
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
