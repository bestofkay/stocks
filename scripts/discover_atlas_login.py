#!/usr/bin/env python3
"""Probe Atlas Portfolios customer portal auth + API surface."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

BASE = "https://app.atlassportfolios.com"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "atlas_discover.json"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def grab(html: str, name: str) -> str:
    m = re.search(rf'id="{re.escape(name)}"[^>]*value="([^"]*)"', html) or re.search(
        rf'name="{re.escape(name)}"[^>]*value="([^"]*)"', html
    )
    return m.group(1) if m else ""


def main() -> int:
    user = os.environ.get("ATLAS_USER", "").strip()
    password = os.environ.get("ATLAS_PASS", "")
    if not user or not password:
        print("Set ATLAS_USER and ATLAS_PASS", file=sys.stderr)
        return 1

    result: dict = {"base": BASE}
    with httpx.Client(
        base_url=BASE,
        timeout=60.0,
        follow_redirects=True,
        headers={"User-Agent": UA, "Accept": "text/html,application/json,*/*"},
    ) as client:
        # Probe entry points
        probes = {}
        for path in ["/", "/customers/", "/customers", "/login", "/Login", "/customers/login", "/Account/Login"]:
            try:
                r = client.get(path)
                title = re.search(r"<title>([^<]+)</title>", r.text, re.I)
                probes[path] = {
                    "status": r.status_code,
                    "final_url": str(r.url),
                    "title": title.group(1).strip() if title else None,
                    "len": len(r.text),
                    "has_password": "password" in r.text.lower(),
                    "has_viewstate": "__VIEWSTATE" in r.text,
                    "has_naya": "NaYaOMS" in r.text or "__NAYA.AUTH" in r.text,
                    "scripts": re.findall(r'src=["\']([^"\']+)["\']', r.text)[:25],
                    "forms": re.findall(r'<form[^>]*action=["\']?([^"\'>\s]*)', r.text, re.I)[:10],
                    "inputs": re.findall(r'name=["\']([^"\']+)["\']', r.text)[:40],
                    "links": re.findall(r'href=["\']([^"\']+)["\']', r.text)[:40],
                    "snippet": re.sub(r"\s+", " ", r.text)[:500],
                }
                print(f"GET {path} -> {r.status_code} {probes[path]['title']} final={r.url}", flush=True)
            except Exception as exc:
                probes[path] = {"error": str(exc)}
                print(f"GET {path} ERR {exc}", flush=True)
        result["probes"] = probes

        # Prefer a page that looks like login
        login_path = None
        login_html = ""
        for path, info in probes.items():
            if isinstance(info, dict) and info.get("has_password") and info.get("status", 500) < 400:
                login_path = path
                login_html = client.get(path).text
                break
        if not login_path:
            # try customers again and capture body for SPA boot
            r = client.get("/customers/")
            login_path = "/customers/"
            login_html = r.text
            result["fallback_customers"] = {
                "status": r.status_code,
                "final_url": str(r.url),
                "snippet": re.sub(r"\s+", " ", r.text)[:1500],
            }

        result["login_path"] = login_path
        title = re.search(r"<title>([^<]+)</title>", login_html, re.I)
        result["login_title"] = title.group(1).strip() if title else None
        result["login_inputs"] = re.findall(r'name=["\']([^"\']+)["\']', login_html)[:50]
        result["meta"] = {
            "has_viewstate": "__VIEWSTATE" in login_html,
            "has_naya": "NaYa" in login_html,
            "has_react": "react" in login_html.lower() or "__NEXT_DATA__" in login_html,
            "has_angular": "ng-app" in login_html or "angular" in login_html.lower(),
            "has_vue": "vue" in login_html.lower(),
            "api_hints": sorted(
                set(
                    re.findall(
                        r"https?://[a-zA-Z0-9._/-]*(?:api|auth|graphql|token)[a-zA-Z0-9._/-]*",
                        login_html,
                        re.I,
                    )
                )
            )[:40],
            "path_hints": sorted(
                set(re.findall(r"/(?:api|auth|v\d+)/[a-zA-Z0-9._/-]+", login_html))
            )[:40],
        }

        # Attempt classic form login if fields look familiar
        lower_names = {n.lower() for n in result["login_inputs"]}
        attempted = []
        if "__viewstate" in lower_names or "txtusername" in lower_names or "username" in lower_names:
            data = {}
            for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION", "__RequestVerificationToken"):
                val = grab(login_html, name)
                if val:
                    data[name] = val
            # common field combos
            combos = [
                {"txtusername": user, "txtpassword": password, "btnsignin": "Sign In", "hdfshown": "login"},
                {"UserName": user, "Password": password},
                {"username": user, "password": password},
                {"Email": user, "Password": password},
                {"userName": user, "password": password, "rememberMe": "false"},
            ]
            for combo in combos:
                payload = {**data, **combo}
                r = client.post(login_path, data=payload, headers={"Referer": urljoin(BASE, login_path), "Origin": BASE})
                attempted.append(
                    {
                        "fields": list(combo.keys()),
                        "status": r.status_code,
                        "final_url": str(r.url),
                        "cookies": list(client.cookies.keys()),
                        "title": (re.search(r"<title>([^<]+)</title>", r.text, re.I) or type("x", (), {"group": lambda *_: None})()).group(1)
                        if re.search(r"<title>([^<]+)</title>", r.text, re.I)
                        else None,
                        "len": len(r.text),
                    }
                )
                print("POST form", combo.keys(), "->", r.status_code, r.url, list(client.cookies.keys()), flush=True)
                # if we got past login, break
                if any(k.lower() in {"authorization", ".aspxauth", "session", "token", "jwt"} for k in client.cookies.keys()) or (
                    "/customers" in str(r.url) and "login" not in str(r.url).lower() and r.status_code == 200 and "password" not in r.text.lower()
                ):
                    break

        # JSON login attempts common for SPAs
        json_attempts = []
        for path, body in [
            ("/api/auth/login", {"username": user, "password": password}),
            ("/api/login", {"username": user, "password": password}),
            ("/api/Account/Login", {"username": user, "password": password}),
            ("/api/customers/login", {"username": user, "password": password}),
            ("/customers/api/login", {"username": user, "password": password}),
            ("/api/token", {"username": user, "password": password, "grant_type": "password"}),
            ("/connect/token", {"username": user, "password": password, "grant_type": "password"}),
            ("/api/v1/auth/login", {"username": user, "password": password}),
            ("/api/v1/login", {"username": user, "password": password}),
            ("/api/Authentication/Login", {"userName": user, "password": password}),
            ("/api/User/Login", {"username": user, "password": password}),
        ]:
            try:
                r = client.post(path, json=body)
                snippet = r.text[:300]
                json_attempts.append(
                    {
                        "path": path,
                        "status": r.status_code,
                        "content_type": r.headers.get("content-type"),
                        "cookies": list(client.cookies.keys()),
                        "snippet": snippet,
                    }
                )
                print(f"POST json {path} -> {r.status_code} {r.headers.get('content-type')}", flush=True)
            except Exception as exc:
                json_attempts.append({"path": path, "error": str(exc)})

        # Also try form-urlencoded token endpoints
        for path in ["/connect/token", "/api/token", "/token"]:
            try:
                r = client.post(
                    path,
                    data={"username": user, "password": password, "grant_type": "password", "client_id": "customers"},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                json_attempts.append(
                    {
                        "path": path + "#form",
                        "status": r.status_code,
                        "content_type": r.headers.get("content-type"),
                        "snippet": r.text[:300],
                    }
                )
                print(f"POST form-token {path} -> {r.status_code}", flush=True)
            except Exception as exc:
                json_attempts.append({"path": path + "#form", "error": str(exc)})

        result["form_attempts"] = attempted
        result["json_attempts"] = json_attempts
        result["cookies_final"] = list(client.cookies.keys())

        # Fetch JS bundles from customers page for API endpoint clues
        cust = client.get("/customers/")
        scripts = re.findall(r'src=["\']([^"\']+\.js[^"\']*)["\']', cust.text)
        result["customer_scripts"] = scripts[:30]
        api_hits = set()
        for src in scripts[:15]:
            url = urljoin(str(cust.url), src)
            try:
                js = client.get(url)
                if js.status_code >= 400:
                    continue
                text = js.text
                for pat in re.findall(
                    r"/[a-zA-Z0-9_\-/]*(?:login|auth|token|portfolio|holding|account|order|deposit|wallet|cash|security|market)[a-zA-Z0-9_\-/]*",
                    text,
                    re.I,
                ):
                    if 3 < len(pat) < 120:
                        api_hits.add(pat)
                for pat in re.findall(r"https?://[a-zA-Z0-9._:\-/]+", text):
                    host = urlparse(pat).hostname or ""
                    if "atlas" in host or "api" in host:
                        api_hits.add(pat)
            except Exception:
                continue
        result["js_api_hints"] = sorted(api_hits)[:200]
        print("js hints", len(result["js_api_hints"]), flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
