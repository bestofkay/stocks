#!/usr/bin/env python3
"""Authenticated Atlas CitiTrader (Caladata Folio) surface discovery."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import httpx

BASE = "https://app.atlassportfolios.com"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "atlas_wa_discover.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"


def hex_hlp85(password: str) -> str:
    h1 = hashlib.sha512(password.encode("utf-8")).hexdigest()
    return hashlib.sha512(h1.encode("utf-8")).hexdigest()


def login(client: httpx.Client, user: str, password: str) -> None:
    client.get("/customers/")
    v = client.post(
        "/folio/authCustomer?a=v",
        content=f"un={user}",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if not v.text.startswith("OK||"):
        raise RuntimeError(f"password-version probe failed: {v.text}")
    auth = client.post(
        "/folio/authCustomer",
        content=f"un={user}&pw={hex_hlp85(password)}",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if not auth.text.startswith("OK||OK"):
        raise RuntimeError(f"login failed: {auth.text}")


def main() -> int:
    user = os.environ.get("ATLAS_USER", "").strip()
    password = os.environ.get("ATLAS_PASS", "")
    if not user or not password:
        print("Set ATLAS_USER and ATLAS_PASS", file=sys.stderr)
        return 1

    out: dict = {"path_hits": [], "api_hits": [], "endpoints": {}, "pages": {}}
    with httpx.Client(
        base_url=BASE,
        timeout=60.0,
        follow_redirects=True,
        headers={"User-Agent": UA, "Origin": BASE, "Referer": f"{BASE}/customers/"},
    ) as client:
        login(client, user, password)
        wa = client.get("/customers/wa/")
        html = wa.text
        (ROOT / "logs" / "atlas_wa.html").write_text(html, encoding="utf-8")
        title_m = re.search(r"<title>([^<]+)</title>", html, re.I)
        out["wa_title"] = title_m.group(1).strip() if title_m else None
        scripts = re.findall(r"""src=["']([^"']+)["']""", html)
        out["scripts"] = scripts
        print("wa title", out["wa_title"], "scripts", scripts, flush=True)

        path_hits: set[str] = set()
        api_hits: set[str] = set()
        for src in scripts:
            url = urljoin(str(wa.url), src)
            js = client.get(url)
            print("js", url, js.status_code, len(js.text), flush=True)
            if js.status_code >= 400:
                continue
            text = js.text
            (ROOT / "logs" / Path(url).name).write_text(text, encoding="utf-8")
            for pat in re.findall(
                r"""["'](/?(?:wa|folio|customers|api|/x/)[^"']{1,160})["']""",
                text,
                re.I,
            ):
                path_hits.add(pat)
            for pat in re.findall(r"""["']([A-Za-z][A-Za-z0-9_]{2,40})["']""", text):
                if any(
                    k in pat.lower()
                    for k in (
                        "port",
                        "hold",
                        "order",
                        "cash",
                        "bal",
                        "dep",
                        "sec",
                        "mark",
                        "acct",
                        "account",
                        "fund",
                        "pos",
                        "trade",
                        "buy",
                        "sell",
                        "cust",
                        "wallet",
                        "statement",
                    )
                ):
                    api_hits.add(pat)
            for m in re.finditer(
                r".{0,40}(?:Request|invoke|post|get|url|cmd|action).{0,80}",
                text,
                re.I,
            ):
                if len(api_hits) < 250:
                    api_hits.add(m.group(0)[:140])

        for pat in re.findall(r"""["'](/?(?:wa|folio)[^"']{1,160})["']""", html, re.I):
            path_hits.add(pat)

        # Folio apps often use /x/ JSON/XML gateway
        for guess in [
            "/x/",
            "/customers/x/",
            "/folio/x/",
            "/wa/x/",
            "/customers/wa/x/",
            "/customers/data/",
            "/folio/cmd",
            "/folio/gateway",
        ]:
            path_hits.add(guess)

        out["path_hits"] = sorted(path_hits)
        out["api_hits"] = sorted(api_hits)[:300]
        print("path_hits", len(out["path_hits"]), flush=True)
        for p in out["path_hits"][:80]:
            print(" ", p, flush=True)

        # Probe pages linked from shell
        for path in [
            "/customers/wa/",
            "/customers/wa/index.html",
            "/customers/main/",
            "/customers/home/",
            "/customers/portfolio/",
            "/customers/orders/",
            "/customers/markets/",
            "/folio/",
        ]:
            r = client.get(path)
            out["pages"][path] = {
                "status": r.status_code,
                "url": str(r.url),
                "len": len(r.content),
                "title": (re.search(r"<title>([^<]+)</title>", r.text, re.I) or type("t", (), {"group": lambda *_: None})()).group(1)
                if re.search(r"<title>([^<]+)</title>", r.text, re.I)
                else None,
                "snip": re.sub(r"\s+", " ", r.text)[:400],
            }
            print("PAGE", path, r.status_code, out["pages"][path]["title"], flush=True)

        # Probe likely endpoints
        candidates = list(out["path_hits"])
        candidates += [
            "/folio/portfolio",
            "/folio/holdings",
            "/folio/accounts",
            "/folio/orders",
            "/folio/cash",
            "/folio/balance",
            "/folio/market",
            "/folio/securities",
            "/folio/deposit",
            "/folio/customer",
            "/folio/customerInfo",
            "/folio/getPortfolio",
            "/folio/getHoldings",
            "/folio/getCash",
            "/folio/getOrders",
            "/folio/getMarket",
            "/customers/folio/portfolio",
            "/customers/wa/data",
            "/x/portfolio",
            "/x/holdings",
            "/x/accounts",
            "/x/orders",
            "/x/cash",
            "/x/market",
            "/x/securities",
            "/x/deposit",
            "/customers/x/portfolio",
            "/customers/x/",
        ]
        seen: set[str] = set()
        for path in candidates:
            if not path or path in seen:
                continue
            seen.add(path)
            url = path if path.startswith("/") else f"/customers/wa/{path}"
            for method, kwargs in [
                ("GET", {}),
                ("POST", {"content": "", "headers": {"Content-Type": "application/x-www-form-urlencoded"}}),
                ("POST", {"content": "{}", "headers": {"Content-Type": "application/json"}}),
            ]:
                try:
                    resp = client.request(method, url, **kwargs)
                except Exception as exc:
                    out["endpoints"][f"{method} {url}"] = {"error": str(exc)}
                    continue
                ct = resp.headers.get("content-type") or ""
                snip = resp.text[:250].replace("\n", " ")
                interesting = (
                    resp.status_code not in {404, 405}
                    and len(resp.content) > 0
                    and ("json" in ct or "text/plain" in ct or snip.startswith("{") or snip.startswith("[") or "||" in snip)
                )
                key = f"{method} {url} {list(kwargs.keys())}"
                if interesting or (resp.status_code < 400 and len(resp.content) > 20 and "html" not in ct):
                    out["endpoints"][key] = {
                        "status": resp.status_code,
                        "ct": ct,
                        "len": len(resp.content),
                        "snip": snip,
                    }
                    print("HIT", key, resp.status_code, ct, snip[:120], flush=True)

        # Parse WA HTML for frame/src patterns and onload
        out["wa_onload"] = re.findall(r"onload=\"([^\"]+)\"", html)
        out["wa_iframe"] = re.findall(r"<iframe[^>]+>", html, re.I)
        out["wa_script_inline"] = re.findall(r"<script[^>]*>(.*?)</script>", html, re.I | re.S)[:5]

    OUT.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
