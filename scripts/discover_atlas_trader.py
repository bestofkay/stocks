#!/usr/bin/env python3
"""Probe Atlas CitiTrader Dojo app (/trader) for API paths; login if possible."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx

BASE = "https://app.atlassportfolios.com"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "atlas_trader_discover.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"


def hex_hlp85(password: str) -> str:
    h1 = hashlib.sha512(password.encode("utf-8")).hexdigest()
    return hashlib.sha512(h1.encode("utf-8")).hexdigest()


def try_login(client: httpx.Client, user: str, password: str, *, attempts: int = 8) -> str:
    """Return auth response text; retries through concurrent-login lock."""
    last = ""
    for i in range(attempts):
        client.get("/customers/")
        v = client.post(
            "/folio/authCustomer?a=v",
            content=f"un={user}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        auth = client.post(
            "/folio/authCustomer",
            content=f"un={user}&pw={hex_hlp85(password)}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        last = auth.text
        print(f"login try {i+1}: {last[:120]!r} cookies={list(client.cookies.keys())}", flush=True)
        if last.startswith("OK||OK"):
            return last
        if "logged in already" in last.lower():
            # probe override flavors then wait
            for path in (
                "/folio/authCustomer?a=o",
                "/folio/authCustomer?a=k",
                "/folio/authCustomer?a=x",
                "/folio/resetlogin?t=c",
            ):
                client.post(
                    path,
                    content=f"un={user}&pw={hex_hlp85(password)}",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            time.sleep(30)
            continue
        break
    return last


def main() -> int:
    user = os.environ.get("ATLAS_USER", "").strip()
    password = os.environ.get("ATLAS_PASS", "")
    if not user or not password:
        print("Set ATLAS_USER and ATLAS_PASS", file=sys.stderr)
        return 1

    out: dict = {"files": {}, "path_hits": [], "endpoints": {}}
    with httpx.Client(
        base_url=BASE,
        timeout=60.0,
        follow_redirects=True,
        headers={"User-Agent": UA, "Origin": BASE, "Referer": f"{BASE}/customers/"},
    ) as client:
        # Public trader assets often readable without auth
        for path in [
            "/trader/",
            "/trader/index.html",
            "/trader/css/main_0017.css",
            "/customers/wa/",
        ]:
            r = client.get(path)
            out["files"][path] = {
                "status": r.status_code,
                "len": len(r.content),
                "ct": r.headers.get("content-type"),
                "url": str(r.url),
                "snip": re.sub(r"\s+", " ", r.text)[:400],
            }
            print("GET", path, r.status_code, len(r.content), flush=True)

        # Login
        auth = try_login(client, user, password, attempts=12)
        out["auth"] = auth
        out["cookies"] = dict(client.cookies)
        if not auth.startswith("OK||OK"):
            OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
            print("login failed; wrote partial", OUT)
            return 1

        wa = client.get("/customers/wa/")
        (ROOT / "logs" / "atlas_wa.html").write_text(wa.text, encoding="utf-8")
        out["wa_len"] = len(wa.text)
        out["wa_snip"] = re.sub(r"\s+", " ", wa.text)[:2000]

        # Extract dojo / script refs
        scripts = re.findall(r"""(?:src|href)=["']([^"']+)["']""", wa.text)
        out["wa_refs"] = scripts
        print("wa refs", scripts[:40], flush=True)

        # Pull main trader JS bundles referenced
        path_hits: set[str] = set()
        for ref in scripts:
            url = urljoin(str(wa.url), ref)
            if not any(url.endswith(ext) for ext in (".js", ".json", ".html")):
                continue
            try:
                resp = client.get(url)
            except Exception as exc:
                print("ref err", url, exc, flush=True)
                continue
            print("asset", url, resp.status_code, len(resp.content), flush=True)
            if resp.status_code >= 400:
                continue
            name = Path(urlparse_path(url)).name or "asset"
            # keep sizeable JS for offline scan
            if url.endswith(".js") and len(resp.text) < 5_000_000:
                (ROOT / "logs" / f"atlas_{name}").write_text(resp.text, encoding="utf-8")
            for pat in re.findall(
                r"""["'](/?(?:folio|trader|customers|wa|x|api)[^"']{0,160})["']""",
                resp.text,
                re.I,
            ):
                path_hits.add(pat)
            for pat in re.findall(
                r"""["']((?:folio|trader|customers|wa|x)/[^"']{1,160})["']""",
                resp.text,
                re.I,
            ):
                path_hits.add(pat)

        # Also scan known trader entry scripts by probing common names
        for guess in [
            "/trader/js/main.js",
            "/trader/js/app.js",
            "/trader/js/trader.js",
            "/trader/js/bootstrap.js",
            "/trader/js/config.js",
            "/trader/js/nls/main.js",
        ]:
            r = client.get(guess)
            print("guess", guess, r.status_code, len(r.content), flush=True)
            if r.status_code < 400 and r.text:
                (ROOT / "logs" / f"atlas_{Path(guess).name}").write_text(r.text, encoding="utf-8")
                for pat in re.findall(
                    r"""["'](/?(?:folio|trader|customers|wa|x|api)[^"']{0,160})["']""",
                    r.text,
                    re.I,
                ):
                    path_hits.add(pat)

        # From WA HTML dojoConfig block
        m = re.search(r"dojoConfig\s*=\s*(\{.*?\})\s*;", wa.text, re.S)
        if m:
            out["dojoConfig_snip"] = m.group(1)[:1500]
            print("dojoConfig", out["dojoConfig_snip"][:300], flush=True)

        out["path_hits"] = sorted(path_hits)
        print("path_hits", len(path_hits), flush=True)
        for p in out["path_hits"][:100]:
            print(" ", p, flush=True)

        # Probe folio data endpoints commonly used by Caladata trader apps
        probes = [
            "/folio/customer",
            "/folio/Customer",
            "/folio/getCustomer",
            "/folio/accounts",
            "/folio/portfolio",
            "/folio/holdings",
            "/folio/positions",
            "/folio/cash",
            "/folio/balance",
            "/folio/orders",
            "/folio/order",
            "/folio/market",
            "/folio/securities",
            "/folio/security",
            "/folio/deposit",
            "/folio/funding",
            "/folio/statement",
            "/folio/statements",
            "/folio/transactions",
            "/folio/watchlist",
            "/folio/session",
            "/folio/ping",
            "/folio/keepalive",
            "/folio/menu",
            "/folio/nav",
            "/folio/bootstrap",
            "/folio/init",
            "/folio/app",
            "/folio/trader",
            "/folio/data",
            "/x/",
            "/trader/x/",
            "/customers/x/",
        ]
        probes += [p if p.startswith("/") else f"/{p}" for p in path_hits if len(p) < 120]
        seen: set[str] = set()
        for path in probes:
            if path in seen or len(path) < 2:
                continue
            seen.add(path)
            for method, kwargs in [
                ("GET", {}),
                (
                    "POST",
                    {
                        "content": "",
                        "headers": {"Content-Type": "application/x-www-form-urlencoded", "Accept": "*/*"},
                    },
                ),
            ]:
                try:
                    resp = client.request(method, path, **kwargs)
                except Exception as exc:
                    continue
                ct = resp.headers.get("content-type") or ""
                snip = resp.text[:300].replace("\n", " ")
                useful = len(resp.content) > 0 and resp.status_code < 500 and (
                    "json" in ct
                    or "text/plain" in ct
                    or snip.startswith("{")
                    or snip.startswith("[")
                    or "||" in snip
                    or (resp.status_code == 200 and "html" not in ct and len(resp.content) < 200000)
                )
                if useful:
                    key = f"{method} {path}"
                    out["endpoints"][key] = {
                        "status": resp.status_code,
                        "ct": ct,
                        "len": len(resp.content),
                        "snip": snip,
                    }
                    print("HIT", key, resp.status_code, ct, snip[:140], flush=True)

    OUT.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


def urlparse_path(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).path


if __name__ == "__main__":
    raise SystemExit(main())
