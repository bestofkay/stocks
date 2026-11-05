#!/usr/bin/env python3
"""Call project /api/v1 endpoints from login → portfolio and save a JSON transcript.

Uses only Portfolio Aggregator HTTP APIs (not Afrinvestor URLs).
Broker credentials come from AFR_USER / AFR_PASS env vars (redacted in the log).
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
OUT = ROOT / "logs" / "project_api_full_flow.json"


def redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in {"password", "access_token", "refresh_token", "pin"} and isinstance(v, str) and v:
                if lk in {"password", "pin"}:
                    out[k] = "[REDACTED]"
                else:
                    out[k] = v[:16] + "...[REDACTED]"
            elif lk in {"account_number", "nuban"} and isinstance(v, str) and len(v) > 4:
                out[k] = v[:2] + "***" + v[-2:]
            elif lk == "credentials" and isinstance(v, dict):
                out[k] = redact(v)
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


class Flow:
    def __init__(self) -> None:
        self.steps: list[dict] = []
        self.started = datetime.now(timezone.utc).isoformat()

    def record(self, *, title: str, method: str, path: str, request_body, status: int, response_body, headers=None):
        self.steps.append(
            {
                "step": len(self.steps) + 1,
                "title": title,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "request": {
                    "method": method,
                    "url": f"{BASE}{path}",
                    "headers": headers or {},
                    "body": redact(request_body),
                },
                "response": {
                    "status_code": status,
                    "body": redact(response_body),
                },
            }
        )
        print(f"[{len(self.steps)}] {method} {path} -> {status} ({title})")


def main() -> int:
    afr_user = os.environ.get("AFR_USER", "").strip()
    afr_pass = os.environ.get("AFR_PASS", "")
    if not afr_user or not afr_pass:
        print("Set AFR_USER and AFR_PASS", file=sys.stderr)
        return 1

    app_email = os.environ.get("APP_EMAIL", f"demo+{uuid.uuid4().hex[:8]}@example.com")
    app_password = os.environ.get("APP_PASSWORD", "DemoPassphrase12!")

    flow = Flow()
    token = None

    with httpx.Client(base_url=BASE, timeout=120.0) as client:
        # 1 health
        r = client.get("/health")
        flow.record(title="Health check", method="GET", path="/health", request_body=None, status=r.status_code, response_body=r.json())

        # 2 register
        body = {"email": app_email, "password": app_password}
        r = client.post("/api/v1/auth/register", json=body)
        try:
            resp = r.json()
        except Exception:
            resp = {"raw": r.text}
        flow.record(
            title="Register app user",
            method="POST",
            path="/api/v1/auth/register",
            request_body=body,
            status=r.status_code,
            response_body=resp,
        )
        if r.status_code not in (201, 409):
            _write(flow, success=False)
            return 1

        # 3 login
        body = {"email": app_email, "password": app_password}
        r = client.post("/api/v1/auth/login", json=body)
        resp = r.json()
        flow.record(
            title="Login (get access token)",
            method="POST",
            path="/api/v1/auth/login",
            request_body=body,
            status=r.status_code,
            response_body=resp,
        )
        if r.status_code != 200:
            _write(flow, success=False)
            return 1
        token = resp["access_token"]
        auth = {"Authorization": f"Bearer {token}"}

        # 4 me
        r = client.get("/api/v1/auth/me", headers=auth)
        flow.record(
            title="Get current user",
            method="GET",
            path="/api/v1/auth/me",
            request_body=None,
            status=r.status_code,
            response_body=r.json(),
            headers={"Authorization": "Bearer <access_token>"},
        )

        # 5 providers
        r = client.get("/api/v1/providers")
        flow.record(
            title="List providers",
            method="GET",
            path="/api/v1/providers",
            request_body=None,
            status=r.status_code,
            response_body=r.json(),
        )

        # 6 provider detail
        r = client.get("/api/v1/providers/afrinvestor")
        flow.record(
            title="Get Afrinvestor provider metadata",
            method="GET",
            path="/api/v1/providers/afrinvestor",
            request_body=None,
            status=r.status_code,
            response_body=r.json(),
        )

        # 7 create connection
        body = {
            "provider_id": "afrinvestor",
            "display_name": "Afrinvestor Main",
            "credentials": {"username": afr_user, "password": afr_pass},
            "options": {},
        }
        r = client.post("/api/v1/connections", headers=auth, json=body)
        resp = r.json()
        flow.record(
            title="Create broker connection",
            method="POST",
            path="/api/v1/connections",
            request_body=body,
            status=r.status_code,
            response_body=resp,
            headers={"Authorization": "Bearer <access_token>", "Content-Type": "application/json"},
        )
        if r.status_code != 201:
            _write(flow, success=False)
            return 1
        connection_id = resp["id"]

        # 8 list connections
        r = client.get("/api/v1/connections", headers=auth)
        flow.record(
            title="List connections",
            method="GET",
            path="/api/v1/connections",
            request_body=None,
            status=r.status_code,
            response_body=r.json(),
            headers={"Authorization": "Bearer <access_token>"},
        )

        # 9 get connection
        r = client.get(f"/api/v1/connections/{connection_id}", headers=auth)
        flow.record(
            title="Get connection",
            method="GET",
            path=f"/api/v1/connections/{connection_id}",
            request_body=None,
            status=r.status_code,
            response_body=r.json(),
            headers={"Authorization": "Bearer <access_token>"},
        )

        # 10 sync
        r = client.post(f"/api/v1/connections/{connection_id}/sync", headers=auth)
        resp = r.json()
        flow.record(
            title="Start sync",
            method="POST",
            path=f"/api/v1/connections/{connection_id}/sync",
            request_body=None,
            status=r.status_code,
            response_body=resp,
            headers={"Authorization": "Bearer <access_token>"},
        )
        if r.status_code not in (200, 202):
            _write(flow, success=False)
            return 1
        sync_id = resp["sync_id"]

        # 11 poll sync until done
        final_sync = None
        for _ in range(60):
            r = client.get(f"/api/v1/syncs/{sync_id}", headers=auth)
            final_sync = r.json()
            status = final_sync.get("status")
            if status in {"completed", "completed_with_warnings", "failed", "cancelled"}:
                break
            time.sleep(2)
        flow.record(
            title="Poll sync status (final)",
            method="GET",
            path=f"/api/v1/syncs/{sync_id}",
            request_body=None,
            status=r.status_code,
            response_body=final_sync,
            headers={"Authorization": "Bearer <access_token>"},
        )
        if not final_sync or final_sync.get("status") not in {"completed", "completed_with_warnings"}:
            _write(flow, success=False)
            return 1

        # 12 portfolio summary
        r = client.get("/api/v1/portfolio/summary", headers=auth)
        flow.record(
            title="Portfolio summary",
            method="GET",
            path="/api/v1/portfolio/summary",
            request_body=None,
            status=r.status_code,
            response_body=r.json(),
            headers={"Authorization": "Bearer <access_token>"},
        )

        # 13 accounts
        r = client.get("/api/v1/portfolio/accounts", headers=auth)
        flow.record(
            title="Portfolio accounts",
            method="GET",
            path="/api/v1/portfolio/accounts",
            request_body=None,
            status=r.status_code,
            response_body=r.json(),
            headers={"Authorization": "Bearer <access_token>"},
        )

        # 14 holdings
        r = client.get("/api/v1/portfolio/holdings", headers=auth)
        flow.record(
            title="Portfolio holdings",
            method="GET",
            path="/api/v1/portfolio/holdings",
            request_body=None,
            status=r.status_code,
            response_body=r.json(),
            headers={"Authorization": "Bearer <access_token>"},
        )

        # 15 by-broker
        r = client.get("/api/v1/portfolio/by-broker", headers=auth)
        flow.record(
            title="Portfolio by broker",
            method="GET",
            path="/api/v1/portfolio/by-broker",
            request_body=None,
            status=r.status_code,
            response_body=r.json(),
            headers={"Authorization": "Bearer <access_token>"},
        )

        # 16 by-security
        r = client.get("/api/v1/portfolio/by-security", headers=auth)
        flow.record(
            title="Portfolio by security",
            method="GET",
            path="/api/v1/portfolio/by-security",
            request_body=None,
            status=r.status_code,
            response_body=r.json(),
            headers={"Authorization": "Bearer <access_token>"},
        )

        # 17 history
        r = client.get("/api/v1/portfolio/history", headers=auth)
        flow.record(
            title="Portfolio history",
            method="GET",
            path="/api/v1/portfolio/history",
            request_body=None,
            status=r.status_code,
            response_body=r.json(),
            headers={"Authorization": "Bearer <access_token>"},
        )

        # 18 deposit instructions (dedicated VA — no payment initiation)
        path = f"/api/v1/deposits/instructions?connection_id={connection_id}"
        r = client.get(path, headers=auth)
        try:
            resp = r.json()
        except Exception:
            resp = {"raw": r.text}
        flow.record(
            title="Deposit instructions (dedicated virtual account)",
            method="GET",
            path=path,
            request_body=None,
            status=r.status_code,
            response_body=resp,
            headers={"Authorization": "Bearer <access_token>"},
        )
        if r.status_code != 200:
            _write(flow, success=False, app_email=app_email)
            return 1

        # 19 markets
        path = f"/api/v1/markets?connection_id={connection_id}"
        r = client.get(path, headers=auth)
        try:
            resp = r.json()
        except Exception:
            resp = {"raw": r.text}
        flow.record(
            title="List tradeable markets",
            method="GET",
            path=path,
            request_body=None,
            status=r.status_code,
            response_body=resp,
            headers={"Authorization": "Bearer <access_token>"},
        )
        if r.status_code != 200:
            _write(flow, success=False, app_email=app_email)
            return 1

        # 20 instruments (first page / optional search)
        instr_path = (
            f"/api/v1/markets/instruments?connection_id={connection_id}"
            f"&market_id=NGX&per_page=20&page=1"
        )
        r = client.get(instr_path, headers=auth)
        try:
            resp = r.json()
        except Exception:
            resp = {"raw": r.text}
        flow.record(
            title="List tradeable instruments (NGX sample page)",
            method="GET",
            path=instr_path,
            request_body=None,
            status=r.status_code,
            response_body=resp,
            headers={"Authorization": "Bearer <access_token>"},
        )
        if r.status_code != 200:
            _write(flow, success=False, app_email=app_email)
            return 1

        # 21 place order (opt-in — places a real broker order when RECORD_LIVE_ORDER=1)
        record_order = os.environ.get("RECORD_LIVE_ORDER", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        order_symbol = os.environ.get("ORDER_SYMBOL", "ACCESSCORP").strip().upper()
        order_side = os.environ.get("ORDER_SIDE", "buy").strip().lower()
        order_qty = os.environ.get("ORDER_QTY", "1").strip()
        order_body = {
            "connection_id": connection_id,
            "side": order_side,
            "symbol": order_symbol,
            "quantity": order_qty,
            "order_type": "market",
        }
        pin = os.environ.get("AFR_PIN", "").strip()
        if pin:
            order_body["pin"] = pin

        if record_order:
            r = client.post("/api/v1/orders", headers=auth, json=order_body)
            try:
                resp = r.json()
            except Exception:
                resp = {"raw": r.text}
            flow.record(
                title="Place equity order (live)",
                method="POST",
                path="/api/v1/orders",
                request_body=order_body,
                status=r.status_code,
                response_body=resp,
                headers={
                    "Authorization": "Bearer <access_token>",
                    "Content-Type": "application/json",
                },
            )
            if r.status_code not in (200, 201):
                _write(flow, success=False, app_email=app_email)
                return 1
        else:
            # Document the endpoint without moving money.
            flow.record(
                title="Place equity order (skipped — set RECORD_LIVE_ORDER=1 to execute)",
                method="POST",
                path="/api/v1/orders",
                request_body=order_body,
                status=0,
                response_body={
                    "skipped": True,
                    "reason": (
                        "Live order not placed. Re-run with RECORD_LIVE_ORDER=1 "
                        "(optional ORDER_SYMBOL / ORDER_SIDE / ORDER_QTY / AFR_PIN) "
                        "to capture a real broker response."
                    ),
                    "example_request": order_body,
                },
                headers={
                    "Authorization": "Bearer <access_token>",
                    "Content-Type": "application/json",
                },
            )

    _write(flow, success=True, app_email=app_email)
    return 0


def _write(flow: Flow, *, success: bool, app_email: str | None = None) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "recorded_at": flow.started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "success": success,
        "base_url": BASE,
        "scope": (
            "Portfolio Aggregator project HTTP endpoints only (/api/v1/*). "
            "Includes auth, connections, sync, portfolio, deposits/instructions, and orders. "
            "No direct Afrinvestor URLs."
        ),
        "app_email": app_email,
        "secrets_note": (
            "passwords, pins, full tokens, and account numbers redacted/partially masked in this file"
        ),
        "steps": flow.steps,
    }
    OUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)


if __name__ == "__main__":
    raise SystemExit(main())
