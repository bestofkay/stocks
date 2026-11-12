#!/usr/bin/env python3
"""Probe MDS / appserver Chapel Hill endpoints for order placement."""

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

OUT = ROOT / "logs" / "investnaija_mds_probe.json"


def encrypt_password(pw: str) -> str:
    der = base64.b64decode(ap.PASSWORD_PUBLIC_KEY_B64)
    key = serialization.load_der_public_key(der)
    ct = key.encrypt(
        pw.encode("utf-8"),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return base64.b64encode(ct).decode("ascii")


async def main() -> int:
    email = os.environ["IN_USER"].strip()
    password = os.environ["IN_PASS"]
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": ap.APP_ORIGIN,
        "Referer": ap.APP_ORIGIN + "/",
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json",
    }
    results = []
    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        login = (await client.post(ap.LOGIN, json={"email": email, "password": encrypt_password(password), "rememberMe": True})).json()
        token, uuid_token = login["token"], login["uuid_token"]
        auth_variants = [
            ("jwt+uuid", {**headers, "Authorization": token, "x-uuid-token": uuid_token}),
            ("bearer+uuid", {**headers, "Authorization": f"Bearer {token}", "x-uuid-token": uuid_token}),
            ("jwt", {**headers, "Authorization": token}),
        ]
        p = (await client.get(ap.TRADE_PORTFOLIOS, headers=auth_variants[0][1])).json()["data"][0]
        px = 7.6
        body = {
            "portfolioId": p["id"],
            "securityId": "JAIZBANK",
            "securityName": "JAIZBANK",
            "quantityRequested": 1,
            "orderType": "LIMIT",
            "priceType": "LIMIT",
            "side": "BUY",
            "limitPrice": px,
            "marketPrice": px,
        }
        paths = [
            "https://mds.zanibal.com/mds/rest/api/v1/orders",
            "https://mds.zanibal.com/mds/rest/api/v1/trade-orders",
            "https://mds.zanibal.com/mds/rest/api/v1/trades/orders",
            "https://mds.zanibal.com/mds/rest/api/v1/equity/orders",
            "https://appserver.chapelhilldenham.com/api/v1/orders",
            "https://appserver.chapelhilldenham.com/mds/rest/api/v1/orders",
            "https://appserver.chapelhilldenham.com/investing/api/v1/orders",
            "https://invest-chd.zanibal.com/investing/api/v1/orders",
            "https://invest-chd.zanibal.com/mds/rest/api/v1/orders",
        ]
        for url in paths:
            for aname, ah in auth_variants:
                hdrs = {**ah, "x-secure-token": p["signature"]}
                for method in ("POST", "GET"):
                    if method == "GET":
                        rr = await client.get(url, headers=hdrs)
                    else:
                        rr = await client.post(url, headers=hdrs, json=body)
                    if rr.status_code == 404:
                        continue
                    try:
                        jb = rr.json()
                    except Exception:
                        jb = {"raw": rr.text[:200]}
                    print(method, url.split(".com")[-1][:50], aname, rr.status_code, str(jb)[:160])
                    results.append({"method": method, "url": url, "auth": aname, "status": rr.status_code, "body": str(jb)[:300]})

        # Also try create with Decimal-like string in a way CHD might coerce:
        # some validators use Number(x) and reject NaN from objects but accept numeric strings after coerce
        # We know strings fail. Try limitPrice as numpy-like? no.
        
        # Try multipart
        secure = {**auth_variants[0][1], "x-secure-token": p["signature"]}
        files = {k: (None, str(v)) for k, v in {
            "portfolioId": p["id"],
            "securityId": "019f669c-41f8-717b-9801-c823274c738f",
            "securityName": "JAIZBANK",
            "quantityRequested": 1,
            "orderType": "LIMIT",
            "priceType": "LIMIT",
            "side": "BUY",
            "marketPrice": px,
            "limitPrice": px,
            "orderTermId": 3680441,
            "orderTermName": "0001000101",
        }.items()}
        # httpx multipart with None filename sends as form field
        rr = await client.post(ap.CREATE_ORDER, headers={k: v for k, v in secure.items() if k != "Content-Type"}, files=files)
        print("multipart", rr.status_code, rr.text[:200])
        results.append({"multipart": rr.status_code, "text": rr.text[:200]})

    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
