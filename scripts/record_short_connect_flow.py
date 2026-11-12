#!/usr/bin/env python3
"""Demonstrate the shortened client path: login → connect(+sync) → ready → portfolio.

Broker credentials: FV_USER / FV_PASS (defaults to Fundvine). Override with PROVIDER.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000")
PROVIDER = os.environ.get("PROVIDER", "fundvine")
OUT = ROOT / "logs" / "short_connect_flow.json"


def _json(r: httpx.Response):
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}


def main() -> int:
    broker_user = os.environ.get("FV_USER") or os.environ.get("BROKER_USER") or ""
    broker_pass = os.environ.get("FV_PASS") or os.environ.get("BROKER_PASS") or ""
    if not broker_user or not broker_pass:
        print("Set FV_USER and FV_PASS (or BROKER_USER / BROKER_PASS)", file=sys.stderr)
        return 1

    app_email = os.environ.get("APP_EMAIL", f"short+{uuid.uuid4().hex[:8]}@example.com")
    app_password = os.environ.get("APP_PASSWORD", "DemoPassphrase12!")
    steps: list[dict] = []
    started = datetime.now(timezone.utc).isoformat()

    def record(title: str, method: str, path: str, status: int, body):
        steps.append(
            {
                "step": len(steps) + 1,
                "title": title,
                "method": method,
                "path": path,
                "status": status,
                "body": body,
            }
        )
        print(f"[{len(steps)}] {method} {path} -> {status} ({title})")

    with httpx.Client(base_url=BASE, timeout=180.0) as client:
        r = client.post("/api/v1/auth/register", json={"email": app_email, "password": app_password})
        record("register", "POST", "/api/v1/auth/register", r.status_code, _json(r))
        if r.status_code not in (201, 409):
            _write(started, False, steps, app_email)
            return 1

        r = client.post("/api/v1/auth/login", json={"email": app_email, "password": app_password})
        login = _json(r)
        record("login", "POST", "/api/v1/auth/login", r.status_code, {
            "token_type": login.get("token_type"),
            "expires_in": login.get("expires_in"),
            "syncs_started": login.get("syncs_started"),
            "access_token": (login.get("access_token") or "")[:16] + "...",
        })
        if r.status_code != 200:
            _write(started, False, steps, app_email)
            return 1
        auth = {"Authorization": f"Bearer {login['access_token']}"}

        body = {
            "provider_id": PROVIDER,
            "display_name": f"{PROVIDER} Main",
            "credentials": {"username": broker_user, "password": broker_pass},
            "sync": True,
        }
        r = client.post("/api/v1/connections", headers=auth, json=body)
        created = _json(r)
        record(
            "connect_and_sync",
            "POST",
            "/api/v1/connections",
            r.status_code,
            {
                "id": created.get("id"),
                "status": created.get("status"),
                "sync": created.get("sync"),
                "ready_url": created.get("ready_url"),
                "portfolio_ready": created.get("portfolio_ready"),
            },
        )
        if r.status_code != 201:
            _write(started, False, steps, app_email)
            return 1

        connection_id = created["id"]
        ready_url = created.get("ready_url") or f"/api/v1/connections/{connection_id}/ready"
        ready = None
        for _ in range(90):
            r = client.get(ready_url, headers=auth)
            ready = _json(r)
            if ready.get("ready") or (ready.get("latest_sync") or {}).get("status") in {
                "failed",
                "cancelled",
                "waiting_for_user",
            }:
                break
            time.sleep(2)
        record("wait_until_ready", "GET", ready_url, r.status_code, ready)
        if not ready or not ready.get("ready"):
            _write(started, False, steps, app_email)
            return 1

        for title, path in (
            ("portfolio_summary", "/api/v1/portfolio/summary"),
            ("portfolio_accounts", "/api/v1/portfolio/accounts"),
            ("portfolio_holdings", "/api/v1/portfolio/holdings"),
            ("deposit_instructions", f"/api/v1/deposits/instructions?connection_id={connection_id}"),
            ("markets", f"/api/v1/markets?connection_id={connection_id}"),
        ):
            r = client.get(path, headers=auth)
            record(title, "GET", path, r.status_code, _json(r))
            if r.status_code != 200:
                _write(started, False, steps, app_email)
                return 1

    _write(started, True, steps, app_email)
    return 0


def _write(started: str, success: bool, steps: list, app_email: str) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "recorded_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "success": success,
                "provider_id": PROVIDER,
                "scope": "Short client path: login → connect(+sync) → ready → portfolio reads",
                "app_email": app_email,
                "steps": steps,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print("Wrote", OUT, "success=", success)


if __name__ == "__main__":
    raise SystemExit(main())
