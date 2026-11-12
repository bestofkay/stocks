#!/usr/bin/env python3
"""Inspect InvestNaija login response headers for JWT / uuid tokens."""

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

OUT = ROOT / "logs" / "investnaija_login_headers.json"


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


async def main() -> int:
    email = os.environ["IN_USER"].strip()
    password = os.environ["IN_PASS"]
    enc = encrypt_password(password)
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": ap.APP_ORIGIN,
        "Referer": ap.APP_ORIGIN + "/",
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        r = await client.post(
            ap.LOGIN,
            json={"email": email, "password": enc, "rememberMe": True},
        )
        hdrs = {k: (v[:40] + "...") if len(v) > 40 else v for k, v in r.headers.items()}
        body = r.json()
        # try Authorization from body variants again
        print("status", r.status_code)
        print("headers", json.dumps(hdrs, indent=2))
        print("body keys", list(body.keys()) if isinstance(body, dict) else type(body))
        # Try common Zanibal header names returned
        token_candidates = {
            "authorization": r.headers.get("authorization") or r.headers.get("Authorization"),
            "x-uuid-token": r.headers.get("x-uuid-token") or r.headers.get("X-Uuid-Token"),
            "x-access-token": r.headers.get("x-access-token"),
            "x-auth-token": r.headers.get("x-auth-token"),
            "set-cookie": r.headers.get("set-cookie"),
        }
        print("token_candidates", {k: bool(v) for k, v in token_candidates.items()})

        # Maybe tokens are nested under zanibal
        zan = body.get("zanibal") if isinstance(body, dict) else None
        print("zanibal type", type(zan), list(zan.keys()) if isinstance(zan, dict) else zan)

        # Try using body fields that look like tokens (long strings)
        long_strs = {}
        if isinstance(body, dict):
            for k, v in body.items():
                if isinstance(v, str) and len(v) > 40:
                    long_strs[k] = v[:20] + "..."
                if isinstance(v, dict):
                    for k2, v2 in v.items():
                        if isinstance(v2, str) and len(v2) > 40:
                            long_strs[f"{k}.{k2}"] = v2[:20] + "..."
        print("long_strs", long_strs)

        OUT.write_text(
            json.dumps(
                {
                    "status": r.status_code,
                    "headers": hdrs,
                    "body_keys": list(body.keys()) if isinstance(body, dict) else None,
                    "token_candidates_present": {k: bool(v) for k, v in token_candidates.items()},
                    "zanibal": zan if not isinstance(zan, dict) else {kk: (str(vv)[:40] if not isinstance(vv, (dict, list)) else type(vv).__name__) for kk, vv in zan.items()},
                    "long_strs": long_strs,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print("Wrote", OUT)

        # If Authorization header present, probe portfolios
        auth = token_candidates["authorization"]
        uuid_t = token_candidates["x-uuid-token"]
        # Sometimes UUID is a body field like id? try body id as uuid? unlikely
        # Search JS for login response mapping - fetch main bundle later
        if auth:
            h = dict(headers)
            h["Authorization"] = auth if not auth.lower().startswith("bearer") else auth.split(" ", 1)[-1]
            if uuid_t:
                h["x-uuid-token"] = uuid_t
            for path in [ap.PROFILE, ap.TRADE_PORTFOLIOS, ap.WALLET_BALANCE]:
                rr = await client.get(path, headers=h)
                print("probe", path.split("/")[-1], rr.status_code)
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
