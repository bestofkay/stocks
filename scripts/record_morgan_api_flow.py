#!/usr/bin/env python3
"""Call project /api/v1 endpoints for Morgan Capital and save a JSON transcript.

Broker credentials: MORGAN_USER / MORGAN_PASS (redacted in the log).
Optional live order: RECORD_LIVE_ORDER=1 with ORDER_SYMBOL / ORDER_SIDE / ORDER_QTY.
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
OUT = ROOT / "logs" / "morgan_api_full_flow.json"
PROVIDER = "morgan"


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


def _json(r: httpx.Response):
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}


def main() -> int:
    morgan_user = os.environ.get("MORGAN_USER", "").strip()
    morgan_pass = os.environ.get("MORGAN_PASS", "")
    if not morgan_user or not morgan_pass:
        print("Set MORGAN_USER and MORGAN_PASS", file=sys.stderr)
        return 1

    app_email = os.environ.get("APP_EMAIL", f"morgan+{uuid.uuid4().hex[:8]}@example.com")
    app_password = os.environ.get("APP_PASSWORD", "DemoPassphrase12!")
    # Sync + Turnstile can take several minutes
    timeout = float(os.environ.get("HTTP_TIMEOUT", "300"))

    flow = Flow()

    with httpx.Client(base_url=BASE, timeout=timeout) as client:
        r = client.get("/health")
        flow.record(title="Health check", method="GET", path="/health", request_body=None, status=r.status_code, response_body=_json(r))

        body = {"email": app_email, "password": app_password}
        r = client.post("/api/v1/auth/register", json=body)
        flow.record(title="Register app user", method="POST", path="/api/v1/auth/register", request_body=body, status=r.status_code, response_body=_json(r))
        if r.status_code not in (201, 409):
            _write(flow, success=False, app_email=app_email)
            return 1

        r = client.post("/api/v1/auth/login", json=body)
        resp = _json(r)
        flow.record(title="Login (get access token)", method="POST", path="/api/v1/auth/login", request_body=body, status=r.status_code, response_body=resp)
        if r.status_code != 200:
            _write(flow, success=False, app_email=app_email)
            return 1
        token = resp["access_token"]
        auth = {"Authorization": f"Bearer {token}"}

        r = client.get("/api/v1/auth/me", headers=auth)
        flow.record(
            title="Get current user",
            method="GET",
            path="/api/v1/auth/me",
            request_body=None,
            status=r.status_code,
            response_body=_json(r),
            headers={"Authorization": "Bearer <access_token>"},
        )

        r = client.get("/api/v1/providers")
        flow.record(title="List providers", method="GET", path="/api/v1/providers", request_body=None, status=r.status_code, response_body=_json(r))

        r = client.get(f"/api/v1/providers/{PROVIDER}")
        flow.record(
            title="Get Morgan provider metadata",
            method="GET",
            path=f"/api/v1/providers/{PROVIDER}",
            request_body=None,
            status=r.status_code,
            response_body=_json(r),
        )

        options: dict = {
            "headless": False,
            "auth_mode": "uc",  # SeleniumBase UC before CDP
            "captcha_timeout_sec": int(os.environ.get("CAPTCHA_WAIT_SEC", "240")),
        }
        # Only reuse storage_state when explicitly requested (stale cookies cause captcha fallback)
        state_env = os.environ.get("MORGAN_STORAGE_STATE", "").strip()
        if state_env:
            state_path = Path(state_env)
            if state_path.is_file():
                try:
                    options["storage_state"] = json.loads(state_path.read_text(encoding="utf-8"))
                    print("Using storage_state from", state_path)
                except Exception as exc:
                    print("Ignoring storage_state:", exc)

        body = {
            "provider_id": PROVIDER,
            "display_name": "Morgan Capital Main",
            "credentials": {"username": morgan_user, "password": morgan_pass},
            "options": options,
        }
        r = client.post("/api/v1/connections", headers=auth, json=body)
        resp = _json(r)
        flow.record(
            title="Create Morgan connection",
            method="POST",
            path="/api/v1/connections",
            request_body=body,
            status=r.status_code,
            response_body=resp,
            headers={"Authorization": "Bearer <access_token>", "Content-Type": "application/json"},
        )
        if r.status_code != 201:
            _write(flow, success=False, app_email=app_email)
            return 1
        connection_id = resp["id"]

        r = client.get("/api/v1/connections", headers=auth)
        flow.record(
            title="List connections",
            method="GET",
            path="/api/v1/connections",
            request_body=None,
            status=r.status_code,
            response_body=_json(r),
            headers={"Authorization": "Bearer <access_token>"},
        )

        r = client.get(f"/api/v1/connections/{connection_id}", headers=auth)
        flow.record(
            title="Get connection",
            method="GET",
            path=f"/api/v1/connections/{connection_id}",
            request_body=None,
            status=r.status_code,
            response_body=_json(r),
            headers={"Authorization": "Bearer <access_token>"},
        )

        r = client.post(f"/api/v1/connections/{connection_id}/sync", headers=auth)
        resp = _json(r)
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
            _write(flow, success=False, app_email=app_email)
            return 1
        sync_id = resp["sync_id"]

        final_sync = None
        # Turnstile + scrape can exceed 2 minutes
        for _ in range(150):
            r = client.get(f"/api/v1/syncs/{sync_id}", headers=auth)
            final_sync = _json(r)
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
            _write(flow, success=False, app_email=app_email)
            return 1

        for title, path in (
            ("Portfolio summary", "/api/v1/portfolio/summary"),
            ("Portfolio accounts", "/api/v1/portfolio/accounts"),
            ("Portfolio holdings", "/api/v1/portfolio/holdings"),
            ("Portfolio by broker", "/api/v1/portfolio/by-broker"),
            ("Portfolio by security", "/api/v1/portfolio/by-security"),
            ("Portfolio history", "/api/v1/portfolio/history"),
        ):
            r = client.get(path, headers=auth)
            flow.record(
                title=title,
                method="GET",
                path=path,
                request_body=None,
                status=r.status_code,
                response_body=_json(r),
                headers={"Authorization": "Bearer <access_token>"},
            )

        path = f"/api/v1/deposits/instructions?connection_id={connection_id}"
        r = client.get(path, headers=auth)
        flow.record(
            title="Deposit instructions (dedicated virtual account)",
            method="GET",
            path=path,
            request_body=None,
            status=r.status_code,
            response_body=_json(r),
            headers={"Authorization": "Bearer <access_token>"},
        )
        if r.status_code != 200:
            _write(flow, success=False, app_email=app_email)
            return 1

        path = f"/api/v1/markets?connection_id={connection_id}"
        r = client.get(path, headers=auth)
        flow.record(
            title="List tradeable markets",
            method="GET",
            path=path,
            request_body=None,
            status=r.status_code,
            response_body=_json(r),
            headers={"Authorization": "Bearer <access_token>"},
        )
        if r.status_code != 200:
            _write(flow, success=False, app_email=app_email)
            return 1

        instr_path = (
            f"/api/v1/markets/instruments?connection_id={connection_id}"
            f"&market_id=NGX&per_page=20&page=1"
        )
        r = client.get(instr_path, headers=auth)
        flow.record(
            title="List tradeable instruments (NGX sample page)",
            method="GET",
            path=instr_path,
            request_body=None,
            status=r.status_code,
            response_body=_json(r),
            headers={"Authorization": "Bearer <access_token>"},
        )
        if r.status_code != 200:
            _write(flow, success=False, app_email=app_email)
            return 1

        record_order = os.environ.get("RECORD_LIVE_ORDER", "").strip().lower() in {"1", "true", "yes"}
        order_symbol = os.environ.get("ORDER_SYMBOL", "GTCO").strip().upper()
        order_side = os.environ.get("ORDER_SIDE", "buy").strip().lower()
        order_qty = os.environ.get("ORDER_QTY", "1").strip()
        order_body = {
            "connection_id": connection_id,
            "side": order_side,
            "symbol": order_symbol,
            "quantity": order_qty,
            "order_type": "market",
        }
        if record_order:
            r = client.post("/api/v1/orders", headers=auth, json=order_body)
            flow.record(
                title="Place equity order (live)",
                method="POST",
                path="/api/v1/orders",
                request_body=order_body,
                status=r.status_code,
                response_body=_json(r),
                headers={"Authorization": "Bearer <access_token>", "Content-Type": "application/json"},
            )
            if r.status_code not in (200, 201):
                _write(flow, success=False, app_email=app_email)
                return 1
        else:
            flow.record(
                title="Place equity order (skipped — set RECORD_LIVE_ORDER=1 to execute)",
                method="POST",
                path="/api/v1/orders",
                request_body=order_body,
                status=0,
                response_body={
                    "skipped": True,
                    "reason": "Live order not placed. Re-run with RECORD_LIVE_ORDER=1.",
                    "example_request": order_body,
                },
                headers={"Authorization": "Bearer <access_token>", "Content-Type": "application/json"},
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
        "provider_id": PROVIDER,
        "scope": (
            "Portfolio Aggregator HTTP endpoints for Morgan Capital "
            "(/api/v1 auth, connections, sync, portfolio, deposits, markets, orders)."
        ),
        "app_email": app_email,
        "secrets_note": "passwords, tokens, and account numbers redacted/partially masked",
        "steps": flow.steps,
    }
    OUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)


if __name__ == "__main__":
    raise SystemExit(main())
