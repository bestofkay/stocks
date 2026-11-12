#!/usr/bin/env python3
"""Authenticated InvestNaija discovery: portfolios, wallet, deposits, orders."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.connectors.investnaija import api_paths as ap  # noqa: E402

OUT = ROOT / "logs" / "investnaija_auth_discovery.json"


def encrypt_password(pw: str) -> str:
    der = base64.b64decode(ap.PASSWORD_PUBLIC_KEY_B64)
    key = serialization.load_der_public_key(der)
    ct = key.encrypt(
        pw.encode("utf-8"),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(ct).decode("ascii")


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in {"token", "uuid_token", "authorization", "x-uuid-token", "x-secure-token", "signature", "password"} and isinstance(v, str) and v:
                out[k] = v[:12] + "...[REDACTED]"
            elif lk in {"accountnumber", "account_number", "nuban"} and isinstance(v, str) and len(v) > 4:
                out[k] = v[:2] + "***" + v[-2:]
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def summarize(body: Any, limit: int = 600) -> Any:
    if body is None:
        return None
    text = json.dumps(redact(body), default=str)
    if len(text) > limit:
        return json.loads(text[:limit] + '..."') if False else text[:limit] + "..."
    return redact(body)


async def main() -> int:
    email = os.environ["IN_USER"].strip()
    password = os.environ["IN_PASS"]
    base_headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": ap.APP_ORIGIN,
        "Referer": ap.APP_ORIGIN + "/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
        "Content-Type": "application/json",
    }
    notes: list[Any] = []
    results: dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=60.0, headers=base_headers) as client:
        r = await client.post(
            ap.LOGIN,
            json={"email": email, "password": encrypt_password(password), "rememberMe": True},
        )
        body = r.json()
        token = body.get("token")
        uuid_token = body.get("uuid_token")
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        notes.append(
            {
                "login_status": r.status_code,
                "message": body.get("message"),
                "has_token": bool(token),
                "has_uuid": bool(uuid_token),
                "twoFactorAuth": data.get("twoFactorAuth"),
                "profile_id": data.get("id"),
                "cscs": data.get("cscs"),
                "chn": data.get("chn"),
            }
        )
        print("login", r.status_code, "token", bool(token), "uuid", bool(uuid_token))
        if not token or not uuid_token:
            OUT.write_text(json.dumps({"ok": False, "notes": notes, "body": redact(body)}, indent=2), encoding="utf-8")
            return 1

        auth = {
            **base_headers,
            "Authorization": str(token),  # no Bearer
            "x-uuid-token": str(uuid_token),
        }

        # Core GETs
        gets = [
            ("PROFILE", ap.PROFILE),
            ("TRADE_PORTFOLIOS", ap.TRADE_PORTFOLIOS),
            ("ASSET_PORTFOLIOS", ap.ASSET_PORTFOLIOS),
            ("WALLET_BALANCE", ap.WALLET_BALANCE),
            ("SECURITIES", ap.SECURITIES),
            ("ORDER_TERMS", ap.ORDER_TERMS),
            ("TXNS", ap.my_transactions(1, 20)),
        ]
        for name, url in gets:
            rr = await client.get(url, headers=auth)
            try:
                jb = rr.json()
            except Exception:
                jb = {"raw": rr.text[:400]}
            results[name] = {"status": rr.status_code, "body": summarize(jb, 1200)}
            print(name, rr.status_code)

        # Deposit / funding path probes
        deposit_paths = [
            ("wallet/accounts", f"{ap.API_BASE}/transactions/wallet/accounts"),
            ("wallet/nuban", f"{ap.API_BASE}/transactions/wallet/nuban"),
            ("wallet/nuban-accounts", f"{ap.API_BASE}/transactions/wallet/nuban-accounts"),
            ("wallet/virtual-account", f"{ap.API_BASE}/transactions/wallet/virtual-account"),
            ("wallet/virtual-accounts", f"{ap.API_BASE}/transactions/wallet/virtual-accounts"),
            ("wallet/funding", f"{ap.API_BASE}/transactions/wallet/funding"),
            ("wallet/funding-details", f"{ap.API_BASE}/transactions/wallet/funding-details"),
            ("wallet/deposit-accounts", f"{ap.API_BASE}/transactions/wallet/deposit-accounts"),
            ("wallet/banks", f"{ap.API_BASE}/transactions/wallet/banks"),
            ("wallet/details", f"{ap.API_BASE}/transactions/wallet/details"),
            ("wallet/info", f"{ap.API_BASE}/transactions/wallet/info"),
            ("payments/virtual-account", f"{ap.API_BASE}/payments/virtual-account"),
            ("payments/nuban", f"{ap.API_BASE}/payments/nuban"),
            ("payments/funding-account", f"{ap.API_BASE}/payments/funding-account"),
            ("customers/nuban", f"{ap.API_BASE}/customers/nuban"),
            ("customers/virtual-account", f"{ap.API_BASE}/customers/virtual-account"),
            ("customers/funding", f"{ap.API_BASE}/customers/funding"),
            ("customers/bank-accounts", f"{ap.API_BASE}/customers/bank-accounts"),
            ("customers/payment-accounts", f"{ap.API_BASE}/customers/payment-accounts"),
            ("funding/accounts", f"{ap.API_BASE}/funding/accounts"),
            ("funding/nuban", f"{ap.API_BASE}/funding/nuban"),
            ("trades/funding", f"{ap.API_BASE}/trades/funding"),
            ("trades/wallet", f"{ap.API_BASE}/trades/wallet"),
            ("trades/cash-accounts", f"{ap.API_BASE}/trades/cash-accounts"),
            ("trades/settlement-accounts", f"{ap.API_BASE}/trades/settlement-accounts"),
            ("bank/accounts", f"{ap.API_BASE}/bank/accounts"),
            ("virtual-accounts", f"{ap.API_BASE}/virtual-accounts"),
            ("nuban", f"{ap.API_BASE}/nuban"),
        ]
        for name, url in deposit_paths:
            for method in ("GET", "POST"):
                if method == "GET":
                    rr = await client.get(url, headers=auth)
                else:
                    rr = await client.post(url, headers=auth, json={})
                key = f"deposit:{method}:{name}"
                try:
                    jb = rr.json()
                except Exception:
                    jb = {"raw": rr.text[:300]}
                if rr.status_code not in (404, 405):
                    results[key] = {"status": rr.status_code, "body": summarize(jb, 800)}
                    print(key, rr.status_code)
                await asyncio.sleep(0.05)

        # Portfolio detail + signature
        trade_body = results.get("TRADE_PORTFOLIOS", {}).get("body")
        # re-fetch full for parsing
        rr = await client.get(ap.TRADE_PORTFOLIOS, headers=auth)
        trade_full = rr.json()
        portfolios = []
        if isinstance(trade_full, dict):
            d = trade_full.get("data")
            if isinstance(d, list):
                portfolios = d
            elif isinstance(d, dict) and isinstance(d.get("items"), list):
                portfolios = d["items"]
            elif isinstance(d, dict) and isinstance(d.get("portfolios"), list):
                portfolios = d["portfolios"]
        elif isinstance(trade_full, list):
            portfolios = trade_full
        notes.append({"trade_count": len(portfolios), "trade_keys": list(portfolios[0].keys()) if portfolios else None})

        for p in portfolios[:3]:
            pid = str(p.get("id") or p.get("portfolioId") or "")
            sig = p.get("signature") or p.get("secureToken") or p.get("secure_token")
            notes.append({"portfolio_id": pid, "has_sig": bool(sig), "sample": redact({k: p.get(k) for k in list(p)[:20]})})
            if not pid:
                continue
            extra = dict(auth)
            if sig:
                extra["x-secure-token"] = str(sig)
            detail_url = ap.trade_portfolio(pid)
            rr = await client.get(detail_url, headers=extra)
            try:
                jb = rr.json()
            except Exception:
                jb = {"raw": rr.text[:400]}
            results[f"TRADE_DETAIL:{pid}"] = {"status": rr.status_code, "body": summarize(jb, 1500)}
            print("TRADE_DETAIL", pid, rr.status_code)

        # Asset portfolios similarly
        rr = await client.get(ap.ASSET_PORTFOLIOS, headers=auth)
        asset_full = rr.json()
        assets = []
        if isinstance(asset_full, dict):
            d = asset_full.get("data")
            if isinstance(d, list):
                assets = d
            elif isinstance(d, dict):
                for k in ("items", "portfolios", "data"):
                    if isinstance(d.get(k), list):
                        assets = d[k]
                        break
        notes.append({"asset_count": len(assets), "asset_keys": list(assets[0].keys()) if assets else None})
        for p in assets[:3]:
            pid = str(p.get("id") or p.get("portfolioId") or "")
            sig = p.get("signature")
            if not pid:
                continue
            extra = dict(auth)
            if sig:
                extra["x-secure-token"] = str(sig)
            url = ap.asset_portfolio_balance(pid)
            rr = await client.get(url, headers=extra)
            try:
                jb = rr.json()
            except Exception:
                jb = {"raw": rr.text[:400]}
            results[f"ASSET_BAL:{pid}"] = {"status": rr.status_code, "body": summarize(jb, 1200)}
            print("ASSET_BAL", pid, rr.status_code)

        # Securities sample + overview
        rr = await client.get(ap.SECURITIES, headers=auth)
        sec = rr.json()
        sec_list = []
        if isinstance(sec, dict):
            d = sec.get("data")
            if isinstance(d, list):
                sec_list = d
            elif isinstance(d, dict):
                for k in ("items", "securities", "data"):
                    if isinstance(d.get(k), list):
                        sec_list = d[k]
                        break
        notes.append({"securities_count": len(sec_list), "sec0": redact(sec_list[0]) if sec_list else None})
        if sec_list:
            sym = sec_list[0].get("symbol") or sec_list[0].get("code") or "GTCO"
            rr = await client.get(ap.security_overview(str(sym)), headers=auth)
            try:
                jb = rr.json()
            except Exception:
                jb = {"raw": rr.text[:400]}
            results[f"SEC_OVERVIEW:{sym}"] = {"status": rr.status_code, "body": summarize(jb, 800)}
            print("SEC_OVERVIEW", sym, rr.status_code)

        # Order terms
        rr = await client.get(ap.ORDER_TERMS, headers=auth)
        try:
            jb = rr.json()
        except Exception:
            jb = {"raw": rr.text[:400]}
        results["ORDER_TERMS_FULL"] = {"status": rr.status_code, "body": summarize(jb, 1500)}

        # Try to find JS bundle hints for deposit endpoints from the SPA
        try:
            app = await client.get(ap.APP_ORIGIN + "/")
            import re

            scripts = re.findall(r'src="([^"]+\.js)"', app.text)
            notes.append({"spa_scripts": scripts[:15]})
            found_endpoints: list[str] = []
            for src in scripts[:20]:
                url = src if src.startswith("http") else ap.APP_ORIGIN + ("/" if not src.startswith("/") else "") + src
                if "invest-chd" in url:
                    continue
                try:
                    js = await client.get(url)
                except Exception:
                    continue
                text = js.text
                for m in re.finditer(r'["\'`](/api/v1/[a-zA-Z0-9_./\-?=&]+)["\']', text):
                    found_endpoints.append(m.group(1))
                for m in re.finditer(r'["\']([a-z]+/(?:wallet|nuban|virtual|funding|deposit|trade-order|portfolio)[^"\']*)["\']', text, re.I):
                    found_endpoints.append(m.group(1))
                # also look for relative path fragments used with API_BASE
                for pat in (r"wallet/[a-zA-Z0-9_\-/]+", r"payments/[a-zA-Z0-9_\-/]+", r"funding/[a-zA-Z0-9_\-/]+", r"trades/[a-zA-Z0-9_\-/]+"):
                    found_endpoints.extend(re.findall(pat, text)[:50])
            uniq = sorted(set(found_endpoints))
            notes.append({"js_endpoint_hints": uniq[:200]})
            print("js hints", len(uniq))
        except Exception as exc:
            notes.append({"js_scan_error": str(exc)})

    OUT.write_text(json.dumps({"ok": True, "notes": notes, "results": results}, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
