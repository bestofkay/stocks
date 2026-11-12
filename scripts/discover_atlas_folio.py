#!/usr/bin/env python3
"""Deep probe Atlas CitiTrader (Caladata Folio Web / wa/)."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import httpx

BASE = "https://app.atlassportfolios.com"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "atlas_folio_discover.json"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def main() -> int:
    user = os.environ.get("ATLAS_USER", "").strip()
    password = os.environ.get("ATLAS_PASS", "")
    if not user or not password:
        print("Set ATLAS_USER and ATLAS_PASS", file=sys.stderr)
        return 1

    result: dict = {}
    with httpx.Client(
        base_url=BASE,
        timeout=60.0,
        follow_redirects=True,
        headers={"User-Agent": UA},
    ) as client:
        login_page = client.get("/customers/")
        result["login_status"] = login_page.status_code
        result["onload"] = re.findall(r"folio\.Web\.initialize\(([^)]+)\)", login_page.text)

        js = client.get("/scripts/folio-web-min008.js")
        result["js_status"] = js.status_code
        result["js_len"] = len(js.text)
        text = js.text
        # Extract string literals that look like endpoints
        strings = re.findall(r'["\'](/?wa/[^"\']+)["\']', text)
        strings += re.findall(r'["\'](wa/[^"\']+)["\']', text)
        strings += re.findall(r'["\']([A-Za-z]+(?:Login|Auth|Account|Portfolio|Order|Holding|Cash|Deposit|Market|Security)[^"\']*)["\']', text)
        result["js_wa_paths"] = sorted(set(strings))[:300]
        # Method-ish tokens near wa/
        result["js_wa_context"] = []
        for m in re.finditer(r".{0,40}wa/[A-Za-z0-9_./\-]+.{0,40}", text):
            result["js_wa_context"].append(m.group(0))
            if len(result["js_wa_context"]) >= 80:
                break
        # Login related snippets
        for key in ["login", "Login", "authenticate", "session", "password", "un", "pw"]:
            idxs = [m.start() for m in re.finditer(re.escape(key), text)][:3]
            snips = []
            for i in idxs:
                snips.append(text[max(0, i - 80) : i + 120])
            if snips:
                result[f"snip_{key}"] = snips

        print("js wa paths", len(result["js_wa_paths"]), flush=True)
        for p in result["js_wa_paths"][:40]:
            print(" ", p, flush=True)

        # Probe wa root and common ops
        wa_probe = {}
        for path in [
            "/wa/",
            "/customers/wa/",
            "/wa/Login",
            "/wa/login",
            "/wa/Authenticate",
            "/wa/Session",
            "/wa/CustomerLogin",
            "/customers/wa/Login",
        ]:
            for method, kwargs in [
                ("GET", {}),
                ("POST", {"json": {"un": user, "pw": password}}),
                ("POST", {"data": {"un": user, "pw": password, "login": "OK"}}),
            ]:
                try:
                    r = client.request(method, path, **kwargs)
                    key = f"{method} {path} {list(kwargs.keys())}"
                    wa_probe[key] = {
                        "status": r.status_code,
                        "ct": r.headers.get("content-type"),
                        "len": len(r.content),
                        "snippet": r.text[:400],
                        "cookies": list(client.cookies.keys()),
                    }
                    print(key, "->", r.status_code, r.headers.get("content-type"), len(r.content), flush=True)
                except Exception as exc:
                    wa_probe[f"{method} {path}"] = {"error": str(exc)}
        result["wa_probe"] = wa_probe

        # Attempt form login as the page does
        form_tries = []
        for action in ["/customers/", "/customers/index.html", "/customers/default.aspx", "/customers/login"]:
            for payload in [
                {"un": user, "pw": password, "login": "OK"},
                {"un": user, "pw": password},
                {"login": "1", "un": user, "pw": password},
            ]:
                r = client.post(action, data=payload, headers={"Referer": f"{BASE}/customers/", "Origin": BASE})
                form_tries.append(
                    {
                        "action": action,
                        "payload_keys": list(payload.keys()),
                        "status": r.status_code,
                        "url": str(r.url),
                        "cookies": dict(client.cookies),
                        "title": (re.search(r"<title>([^<]+)</title>", r.text, re.I) or type("t", (), {"group": lambda *_: ""})()).group(1)
                        if re.search(r"<title>([^<]+)</title>", r.text, re.I)
                        else "",
                        "len": len(r.text),
                        "snippet": re.sub(r"\s+", " ", r.text)[:300],
                    }
                )
                print("FORM", action, payload.keys(), "->", r.status_code, list(client.cookies.keys()), flush=True)
        result["form_tries"] = form_tries

        # Save interesting raw JS excerpts around initialize / login handlers
        for pat in [r"initialize\s*[:=]function", r"function\s*\w*login\w*", r"\.login\s*=", r"wa/"]:
            m = re.search(pat, text, re.I)
            if m:
                i = m.start()
                result.setdefault("js_blocks", {})[pat] = text[max(0, i - 100) : i + 500]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
