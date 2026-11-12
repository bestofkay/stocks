#!/usr/bin/env python3
"""Live discovery of InvestNaija API shapes (redacted transcript)."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.connectors.investnaija import api_paths as ap  # noqa: E402

OUT = ROOT / "logs" / "investnaija_discovery.json"


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


def redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(x in lk for x in ("token", "password", "authorization", "signature", "refresh")) and isinstance(v, str) and len(v) > 8:
                out[k] = v[:12] + "...[REDACTED]"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj[:30]]
    return obj


def pick_list(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    data = payload.get("data", payload)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("content", "items", "portfolios", "records", "result", "securities"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


async def main() -> int:
    email = (os.environ.get("IN_USER") or os.environ.get("BROKER_USER") or "").strip()
    password = os.environ.get("IN_PASS") or os.environ.get("BROKER_PASS") or ""
    if not email or not password:
        print("Set IN_USER and IN_PASS", file=sys.stderr)
        return 1

    notes: list = []
    enc = encrypt_password(password)
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": ap.APP_ORIGIN,
        "Referer": ap.APP_ORIGIN + "/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
        ),
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60.0, headers=headers, follow_redirects=False) as client:
        login = None
        for i, body in enumerate(
            [
                {"email": email, "password": enc, "rememberMe": True},
                {"email": email, "password": password, "rememberMe": True},
                {"username": email, "password": enc, "rememberMe": True},
            ]
        ):
            r = await client.post(ap.LOGIN, json=body)
            try:
                j = r.json()
            except Exception:
                j = {"raw": r.text[:800]}
            notes.append({"login_try": i, "status": r.status_code, "sample": redact(j)})
            print(f"login try {i} -> {r.status_code}")
            if r.status_code < 400 and isinstance(j, dict):
                login = j
                break

        if not login:
            OUT.parent.mkdir(exist_ok=True)
            OUT.write_text(json.dumps({"ok": False, "notes": notes}, indent=2), encoding="utf-8")
            print("LOGIN FAILED — see", OUT)
            return 1

        flat = login.get("data") if isinstance(login.get("data"), dict) else login
        token = flat.get("token") or flat.get("accessToken") or flat.get("access_token") or flat.get("jwt")
        uuid_token = flat.get("uuid_token") or flat.get("uuidToken") or flat.get("uuid")
        refresh = flat.get("refresh_token") or flat.get("refreshToken")
        for nest_key in ("session", "auth", "result", "user"):
            nest = flat.get(nest_key)
            if isinstance(nest, dict):
                token = token or nest.get("token") or nest.get("accessToken")
                uuid_token = uuid_token or nest.get("uuid_token") or nest.get("uuidToken")
                refresh = refresh or nest.get("refresh_token") or nest.get("refreshToken")
        notes.append(
            {
                "flat_keys": list(flat.keys()) if isinstance(flat, dict) else None,
                "token_found": bool(token),
                "uuid_found": bool(uuid_token),
                "refresh_found": bool(refresh),
            }
        )
        print("keys", list(flat.keys()) if isinstance(flat, dict) else flat)
        print("token", bool(token), "uuid", bool(uuid_token))

        auth_headers = dict(headers)
        if token:
            auth_headers["Authorization"] = token
        if uuid_token:
            auth_headers["x-uuid-token"] = str(uuid_token)

        async def get(path, extra=None):
            h = dict(auth_headers)
            if extra:
                h.update(extra)
            r = await client.get(path, headers=h)
            try:
                j = r.json()
            except Exception:
                j = {"raw": r.text[:1000]}
            return r.status_code, j

        async def post(path, body, extra=None):
            h = dict(auth_headers)
            if extra:
                h.update(extra)
            r = await client.post(path, headers=h, json=body)
            try:
                j = r.json()
            except Exception:
                j = {"raw": r.text[:1000]}
            return r.status_code, j

        probes = [
            ("PROFILE", ap.PROFILE),
            ("TRADE_PORTFOLIOS", ap.TRADE_PORTFOLIOS),
            ("ASSET_PORTFOLIOS", ap.ASSET_PORTFOLIOS),
            ("WALLET_BALANCE", ap.WALLET_BALANCE),
            ("SECURITIES", ap.SECURITIES),
            ("ORDER_TERMS", ap.ORDER_TERMS),
            ("TXNS", ap.my_transactions()),
            ("wallet/accounts", f"{ap.API_BASE}/transactions/wallet/accounts"),
            ("wallet/virtual-accounts", f"{ap.API_BASE}/transactions/wallet/virtual-accounts"),
            ("wallet/nuban", f"{ap.API_BASE}/transactions/wallet/nuban"),
            ("wallet/banks", f"{ap.API_BASE}/transactions/wallet/banks"),
            ("customers/accounts", f"{ap.API_BASE}/customers/accounts"),
            ("customers/virtual-accounts", f"{ap.API_BASE}/customers/virtual-accounts"),
            ("funding/accounts", f"{ap.API_BASE}/funding/accounts"),
            ("payments/virtual-accounts", f"{ap.API_BASE}/payments/virtual-accounts"),
            ("wallet/deposit-accounts", f"{ap.API_BASE}/transactions/wallet/deposit-accounts"),
            ("wallet/funding-details", f"{ap.API_BASE}/transactions/wallet/funding-details"),
            ("customers/profile/bank", f"{ap.API_BASE}/customers/profile/bank"),
            ("customers/banks", f"{ap.API_BASE}/customers/banks"),
            ("customers/nuban", f"{ap.API_BASE}/customers/nuban"),
        ]

        results = {}
        for name, path in probes:
            st, j = await get(path)
            results[name] = {"status": st, "body": redact(j)}
            print(name, st)

        portfolios = pick_list(results.get("TRADE_PORTFOLIOS", {}).get("body"))
        notes.append(
            {
                "trade_portfolio_count": len(portfolios),
                "first_keys": list(portfolios[0].keys()) if portfolios else None,
                "first": redact(portfolios[0]) if portfolios else None,
            }
        )

        if portfolios:
            p0 = portfolios[0]
            pid = p0.get("id") or p0.get("portfolioId") or p0.get("uuid")
            sig = p0.get("signature") or p0.get("secureToken") or p0.get("secure_token")
            notes.append({"pid": pid, "sig_found": bool(sig), "sig_keys": [k for k in p0 if "sign" in k.lower() or "secure" in k.lower() or "token" in k.lower()]})
            extra = {"x-secure-token": sig} if sig else None
            if pid:
                st, j = await get(ap.trade_portfolio(str(pid)), extra=extra)
                results["TRADE_PORTFOLIO_DETAIL"] = {"status": st, "body": redact(j)}
                print("DETAIL", st)
                # if signature nested in detail
                if not sig and isinstance(j, dict):
                    data = j.get("data") if isinstance(j.get("data"), dict) else j
                    if isinstance(data, dict):
                        sig = data.get("signature") or data.get("secureToken")
                        notes.append({"sig_from_detail": bool(sig)})
                        if sig:
                            extra = {"x-secure-token": sig}

                st, j = await get(ap.security_overview("ACCESSCORP"))
                results["SECURITY_ACCESSCORP"] = {"status": st, "body": redact(j)}
                print("OVERVIEW", st)

                calc_bodies = [
                    {"side": "BUY", "symbol": "ACCESSCORP", "quantity": 1, "orderType": "MARKET", "portfolioId": pid},
                    {"tradeAction": "BUY", "securityCode": "ACCESSCORP", "units": 1, "orderType": "MARKET", "portfolioId": pid},
                    {"orderSide": "BUY", "ticker": "ACCESSCORP", "qty": 1, "type": "MARKET", "portfolioId": pid},
                    {"side": "BUY", "secId": "ACCESSCORP", "requestedQty": 1, "orderType": "MARKET", "portfolioId": pid, "marketCode": "NGX"},
                    {"buySell": "B", "stockCode": "ACCESSCORP", "quantity": 1, "orderType": "MARKET", "portfolioId": pid},
                ]
                for idx, body in enumerate(calc_bodies):
                    st, j = await post(ap.CALCULATE_PRICE, body, extra=extra)
                    results[f"CALC_{idx}"] = {"status": st, "req": body, "body": redact(j)}
                    print(f"CALC_{idx}", st, list(j.keys()) if isinstance(j, dict) else j)

                # Also dump JS hints for deposit/order from app if reachable
                try:
                    r = await client.get(ap.APP_ORIGIN + "/")
                    results["APP_HOME"] = {"status": r.status_code, "len": len(r.text)}
                except Exception as exc:
                    results["APP_HOME"] = {"error": str(exc)}

        OUT.parent.mkdir(exist_ok=True)
        OUT.write_text(json.dumps({"ok": True, "notes": notes, "results": results}, indent=2, default=str), encoding="utf-8")
        print("Wrote", OUT)
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
