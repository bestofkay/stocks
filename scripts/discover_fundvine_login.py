#!/usr/bin/env python3
"""Discover Fundvine NaYa OMS login form + attempt auth (credentials from env)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://etrade.fundvinesecurities.com"
OUT = ROOT / "logs" / "fundvine_login_probe.txt"


def main() -> int:
    user = os.environ.get("FV_USER", "").strip()
    password = os.environ.get("FV_PASS", "")
    if not user or not password:
        print("Set FV_USER and FV_PASS", file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    with httpx.Client(base_url=BASE, timeout=60.0, follow_redirects=False) as client:
        r = client.get("/Login.aspx")
        lines.append(f"GET /Login.aspx -> {r.status_code} loc={r.headers.get('location')}")
        html = r.text
        # ASP.NET fields
        viewstate = re.search(r'id="__VIEWSTATE"[^>]*value="([^"]*)"', html)
        viewstate_gen = re.search(r'id="__VIEWSTATEGENERATOR"[^>]*value="([^"]*)"', html)
        event_val = re.search(r'id="__EVENTVALIDATION"[^>]*value="([^"]*)"', html)
        req_tok = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]*)"', html)
        form = re.search(r'<form[^>]+>', html, re.I)
        lines.append(f"form_tag={form.group(0) if form else None}")
        inputs = re.findall(r"<input[^>]+>", html, re.I)
        for inp in inputs:
            # redact long values partially
            m = re.search(r'name="([^"]+)"', inp, re.I)
            name = m.group(1) if m else "?"
            vm = re.search(r'value="([^"]*)"', inp, re.I)
            val = vm.group(1) if vm else ""
            shown = val if len(val) < 40 else val[:20] + f"...({len(val)})"
            lines.append(f"input name={name} value={shown}")

        # Also list aspx/ashx/js links of interest
        for href in re.findall(r'(?:href|src|action)=["\']([^"\']+)["\']', html, re.I):
            if any(x in href.lower() for x in (".aspx", ".ashx", ".asmx", "api", "ajax", "service")):
                lines.append(f"ref={href}")

        data = {}
        if viewstate:
            data["__VIEWSTATE"] = viewstate.group(1)
        if viewstate_gen:
            data["__VIEWSTATEGENERATOR"] = viewstate_gen.group(1)
        if event_val:
            data["__EVENTVALIDATION"] = event_val.group(1)
        if req_tok:
            data["__RequestVerificationToken"] = req_tok.group(1)

        # Common ASP.NET login field names from NaYa
        # Probe from HTML for username/password fields
        user_field = None
        pass_field = None
        for inp in inputs:
            nm = re.search(r'name="([^"]+)"', inp, re.I)
            id_ = re.search(r'id="([^"]+)"', inp, re.I)
            typ = re.search(r'type="([^"]+)"', inp, re.I)
            name = nm.group(1) if nm else ""
            iid = id_.group(1) if id_ else ""
            t = (typ.group(1) if typ else "").lower()
            blob = f"{name} {iid}".lower()
            if t == "password" or "password" in blob or "txtpassword" in blob:
                pass_field = name or iid
            elif "username" in blob or "txtusername" in blob or "login" in blob:
                if t in ("text", "email", ""):
                    user_field = name or iid

        lines.append(f"detected user_field={user_field} pass_field={pass_field}")

        # Keep me logged in checkbox?
        remember = None
        for inp in inputs:
            nm = re.search(r'name="([^"]+)"', inp, re.I)
            iid = re.search(r'id="([^"]+)"', inp, re.I)
            blob = f"{nm.group(1) if nm else ''} {iid.group(1) if iid else ''}".lower()
            if "keep" in blob or "remember" in blob:
                remember = nm.group(1) if nm else iid.group(1) if iid else None
        lines.append(f"remember={remember}")

        # Sign-in button
        btn = None
        for m in re.finditer(r'<(?:input|button)[^>]+>', html, re.I):
            tag = m.group(0)
            if re.search(r'btnsignin|signin|login', tag, re.I):
                lines.append(f"btn_tag={tag[:300]}")
                nm = re.search(r'name="([^"]+)"', tag, re.I)
                if nm:
                    btn = nm.group(1)

        if not user_field or not pass_field:
            OUT.write_text("\n".join(lines), encoding="utf-8")
            print("Could not detect login fields; see", OUT)
            return 1

        data[user_field] = user
        data[pass_field] = password
        if remember:
            data[remember] = "on"
        if btn:
            data[btn] = "Sign in"

        r2 = client.post("/Login.aspx?ReturnUrl=%2f", data=data)
        lines.append(f"POST login -> {r2.status_code} loc={r2.headers.get('location')}")
        lines.append(f"cookies={list(client.cookies.keys())}")
        # follow redirect manually
        loc = r2.headers.get("location")
        if loc:
            r3 = client.get(loc)
            lines.append(f"GET {loc} -> {r3.status_code} final_url follow_off")
            lines.append(f"body_snip={r3.text[:500].replace(chr(10),' ')}")
        else:
            # maybe stayed on login with error
            err = re.search(r'login_.*?error[^>]*>.*?([^<]+)', r2.text, re.I | re.S)
            lines.append(f"no_redirect; err_hint={err.group(1).strip() if err else 'none'}")
            # check title
            title = re.search(r"<title>([^<]+)</title>", r2.text, re.I)
            lines.append(f"title={title.group(1).strip() if title else '?'}")
            if "Login" not in (title.group(1) if title else "Login"):
                lines.append("possibly logged in without redirect")
            lines.append(f"body_snip={r2.text[:800].replace(chr(10),' ')}")

        # Try homepage with cookies
        r4 = client.get("/", follow_redirects=True)
        lines.append(f"GET / follow -> {r4.status_code} url={r4.url}")
        title = re.search(r"<title>([^<]+)</title>", r4.text, re.I)
        lines.append(f"home_title={title.group(1).strip() if title else '?'}")
        # Collect .aspx links from home
        pages = sorted(set(re.findall(r'href=["\']([^"\']+\.aspx[^"\']*)["\']', r4.text, re.I)))
        lines.append(f"aspx_count={len(pages)}")
        for p in pages[:80]:
            lines.append(f"page={p}")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(OUT.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
