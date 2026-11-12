#!/usr/bin/env python3
"""Retry Fundvine ASP.NET login with correct field names."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import httpx

BASE = "https://etrade.fundvinesecurities.com"
OUT = Path(__file__).resolve().parents[1] / "logs" / "fundvine_login2.txt"


def main() -> int:
    user = os.environ["FV_USER"].strip()
    password = os.environ["FV_PASS"]
    lines: list[str] = []

    with httpx.Client(base_url=BASE, timeout=60.0, follow_redirects=False) as client:
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
            "txtusername_reset": "",
            "txtemail_reset": "",
        }
        lines.append(f"cookies_before={dict(client.cookies)}")
        r2 = client.post("/Login.aspx", data=data)
        lines.append(f"POST /Login.aspx -> {r2.status_code} loc={r2.headers.get('location')}")
        lines.append(f"set-cookie={r2.headers.get('set-cookie')}")
        lines.append(f"cookies_after={list(client.cookies.keys())}")

        # Look for validation/error messages
        for pat in [
            r'id="[^"]*error[^"]*"[^>]*>([^<]+)',
            r'class="[^"]*error[^"]*"[^>]*>([^<]+)',
            r'lblError[^>]*>([^<]+)',
            r'validation[^>]*>([^<]+)',
            r'alert\(["\']([^"\']+)',
        ]:
            for m in re.finditer(pat, r2.text, re.I):
                t = m.group(1).strip()
                if t:
                    lines.append(f"err={t}")

        title = re.search(r"<title>([^<]+)</title>", r2.text, re.I)
        lines.append(f"title={title.group(1).strip() if title else '?'}")

        # Try ReturnUrl variant
        r = client.get("/Login.aspx?ReturnUrl=%2f")
        html = r.text

        def grab2(name: str) -> str:
            m = re.search(rf'id="{name}"[^>]*value="([^"]*)"', html) or re.search(
                rf'name="{name}"[^>]*value="([^"]*)"', html
            )
            return m.group(1) if m else ""

        data2 = {
            "__VIEWSTATE": grab2("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": grab2("__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": grab2("__EVENTVALIDATION"),
            "__RequestVerificationToken": grab2("__RequestVerificationToken"),
            "txtusername": user,
            "txtpassword": password,
            "chkrememberme": "on",
            "btnsignin": "Sign In",
            "hdfshown": "login",
        }
        r3 = client.post(
            "/Login.aspx?ReturnUrl=%2f",
            data=data2,
            headers={"Referer": f"{BASE}/Login.aspx?ReturnUrl=%2f", "Origin": BASE},
        )
        lines.append(f"POST ReturnUrl -> {r3.status_code} loc={r3.headers.get('location')}")
        title = re.search(r"<title>([^<]+)</title>", r3.text, re.I)
        lines.append(f"title2={title.group(1).strip() if title else '?'}")
        for m in re.finditer(r'id="[^"]*error[^"]*"[^>]*>([^<]*)', r3.text, re.I):
            if m.group(1).strip():
                lines.append(f"err2={m.group(1).strip()}")

        # Also try with __EVENTTARGET
        data3 = dict(data2)
        data3["__EVENTTARGET"] = "btnsignin"
        data3["__EVENTARGUMENT"] = ""
        del data3["btnsignin"]
        r4 = client.post("/Login.aspx?ReturnUrl=%2f", data=data3, headers={"Referer": f"{BASE}/Login.aspx?ReturnUrl=%2f"})
        lines.append(f"POST EVENTTARGET -> {r4.status_code} loc={r4.headers.get('location')}")
        title = re.search(r"<title>([^<]+)</title>", r4.text, re.I)
        lines.append(f"title3={title.group(1).strip() if title else '?'}")

        # Save response HTML for inspection
        (Path(__file__).resolve().parents[1] / "logs" / "fundvine_login_resp.html").write_text(
            r3.text, encoding="utf-8", errors="replace"
        )

        # Follow if any 302
        for label, resp in [("r2", r2), ("r3", r3), ("r4", r4)]:
            loc = resp.headers.get("location")
            if loc:
                rf = client.get(loc)
                lines.append(f"{label} follow {loc} -> {rf.status_code}")

        rh = client.get("/", follow_redirects=True)
        lines.append(f"GET / -> {rh.status_code} url={rh.url}")
        title = re.search(r"<title>([^<]+)</title>", rh.text, re.I)
        lines.append(f"home={title.group(1).strip() if title else '?'}")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(OUT.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
