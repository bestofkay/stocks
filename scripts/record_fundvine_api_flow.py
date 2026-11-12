#!/usr/bin/env python3
"""Call project /api/v1 endpoints for Fundvine and save a JSON transcript.

Broker credentials: FV_USER / FV_PASS (redacted in the log).
Live buy+sell: RECORD_LIVE_ORDER=1 with optional ORDER_SYMBOL / ORDER_QTY / ORDER_TYPE.

    $env:FV_USER='you@email.com'; $env:FV_PASS='...'
    $env:RECORD_LIVE_ORDER='1'
    python scripts\\record_fundvine_api_flow.py
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
OUT = ROOT / "logs" / "fundvine_api_full_flow.json"
PROVIDER = "fundvine"


def redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in {"password", "access_token", "refresh_token", "pin", "token"} and isinstance(v, str) and v:
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


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def main() -> int:
    in_user = os.environ.get("FV_USER", "").strip()
    in_pass = os.environ.get("FV_PASS", "")
    if not in_user or not in_pass:
        print("Set FV_USER and FV_PASS", file=sys.stderr)
        return 1

    app_email = os.environ.get("APP_EMAIL", f"fv+{uuid.uuid4().hex[:8]}@example.com")
    app_password = os.environ.get("APP_PASSWORD", "DemoPassphrase12!")
    timeout = float(os.environ.get("HTTP_TIMEOUT", "180"))

    flow = Flow()

    with httpx.Client(base_url=BASE, timeout=timeout) as client:
        r = client.get("/health")
        flow.record(
            title="Health check",
            method="GET",
            path="/health",
            request_body=None,
            status=r.status_code,
            response_body=_json(r),
        )

        body = {"email": app_email, "password": app_password}
        r = client.post("/api/v1/auth/register", json=body)
        flow.record(
            title="Register app user",
            method="POST",
            path="/api/v1/auth/register",
            request_body=body,
            status=r.status_code,
            response_body=_json(r),
        )
        if r.status_code not in (201, 409):
            _write(flow, success=False, app_email=app_email)
            return 1

        r = client.post("/api/v1/auth/login", json=body)
        resp = _json(r)
        flow.record(
            title="Login (get access token)",
            method="POST",
            path="/api/v1/auth/login",
            request_body=body,
            status=r.status_code,
            response_body=resp,
        )
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
        flow.record(
            title="List providers",
            method="GET",
            path="/api/v1/providers",
            request_body=None,
            status=r.status_code,
            response_body=_json(r),
        )

        r = client.get(f"/api/v1/providers/{PROVIDER}")
        flow.record(
            title="Get Fundvine provider metadata",
            method="GET",
            path=f"/api/v1/providers/{PROVIDER}",
            request_body=None,
            status=r.status_code,
            response_body=_json(r),
        )

        creds = {"username": in_user, "password": in_pass}
        body = {
            "provider_id": PROVIDER,
            "display_name": "Fundvine Main",
            "credentials": creds,
        }
        r = client.post("/api/v1/connections", headers=auth, json=body)
        resp = _json(r)
        flow.record(
            title="Create Fundvine connection",
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

        # Connection create may already have auto-enqueued a sync
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
        sync_id = None
        if r.status_code in (200, 202):
            sync_id = resp.get("sync_id")
        elif r.status_code == 409:
            # Auto-sync already running — pick the latest sync for this connection
            r_list = client.get(f"/api/v1/connections/{connection_id}/syncs", headers=auth)
            runs = _json(r_list)
            flow.record(
                title="List connection syncs (after sync conflict)",
                method="GET",
                path=f"/api/v1/connections/{connection_id}/syncs",
                request_body=None,
                status=r_list.status_code,
                response_body=runs,
                headers={"Authorization": "Bearer <access_token>"},
            )
            if isinstance(runs, list) and runs:
                sync_id = runs[0].get("id")
        if not sync_id:
            _write(flow, success=False, app_email=app_email)
            return 1

        final_sync = None
        for _ in range(90):
            r = client.get(f"/api/v1/syncs/{sync_id}", headers=auth)
            final_sync = _json(r)
            status = final_sync.get("status")
            if status in {"completed", "completed_with_warnings", "failed", "cancelled", "waiting_for_user"}:
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
        if final_sync and final_sync.get("status") == "waiting_for_user":
            print("Sync waiting for MFA — complete MFA via POST /connections/{id}/mfa")
            _write(flow, success=False, app_email=app_email)
            return 2
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
            title="Deposit instructions (Paystack / portal funding)",
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

        order_symbol = os.environ.get("ORDER_SYMBOL", "GTCO").strip().upper()
        order_qty = os.environ.get("ORDER_QTY", "1").strip()
        order_type = os.environ.get("ORDER_TYPE", "limit").strip().lower()
        buy_body = {
            "connection_id": connection_id,
            "side": "buy",
            "symbol": order_symbol,
            "quantity": order_qty,
            "order_type": order_type,
        }
        sell_body = {
            "connection_id": connection_id,
            "side": "sell",
            "symbol": order_symbol,
            "quantity": order_qty,
            "order_type": order_type,
        }
        if order_type == "limit":
            limit = os.environ.get("ORDER_LIMIT_PRICE", "70").strip()
            buy_body["limit_price"] = limit
            sell_body["limit_price"] = os.environ.get("ORDER_SELL_LIMIT", "70").strip()
            sell_body["symbol"] = os.environ.get("ORDER_SELL_SYMBOL", "GTCO").strip().upper()

        if _truthy("RECORD_LIVE_ORDER"):
            r = client.post("/api/v1/orders", headers=auth, json=buy_body)
            flow.record(
                title="Place equity BUY (live)",
                method="POST",
                path="/api/v1/orders",
                request_body=buy_body,
                status=r.status_code,
                response_body=_json(r),
                headers={"Authorization": "Bearer <access_token>", "Content-Type": "application/json"},
            )
            buy_ok = r.status_code in (200, 201)

            r = client.post("/api/v1/orders", headers=auth, json=sell_body)
            flow.record(
                title="Place equity SELL (live)",
                method="POST",
                path="/api/v1/orders",
                request_body=sell_body,
                status=r.status_code,
                response_body=_json(r),
                headers={"Authorization": "Bearer <access_token>", "Content-Type": "application/json"},
            )
            sell_ok = r.status_code in (200, 201)
            if not (buy_ok and sell_ok):
                # Still finish deposit/sync transcript; mark overall success false at end via flag
                flow.record(
                    title="Live order note",
                    method="POST",
                    path="/api/v1/orders",
                    request_body=None,
                    status=0,
                    response_body={
                        "buy_ok": buy_ok,
                        "sell_ok": sell_ok,
                        "note": "Fundvine may reject live orders when cash/holdings are insufficient; deposit/sync still valid.",
                    },
                )
                flow._orders_failed = True  # type: ignore[attr-defined]
        else:
            flow.record(
                title="Place equity orders (skipped — set RECORD_LIVE_ORDER=1)",
                method="POST",
                path="/api/v1/orders",
                request_body={"buy": buy_body, "sell": sell_body},
                status=0,
                response_body={
                    "skipped": True,
                    "reason": "Live orders not placed. Re-run with RECORD_LIVE_ORDER=1.",
                },
                headers={"Authorization": "Bearer <access_token>", "Content-Type": "application/json"},
            )

        # Loan collateral / eligibility (DB holdings only — cash excluded)
        r = client.get("/api/v1/loan/collateral", headers=auth)
        collateral = _json(r)
        flow.record(
            title="Loan collateral (all holdings)",
            method="GET",
            path="/api/v1/loan/collateral",
            request_body=None,
            status=r.status_code,
            response_body=collateral,
            headers={"Authorization": "Bearer <access_token>"},
        )
        if r.status_code != 200:
            _write(flow, success=False, app_email=app_email)
            return 1

        r = client.get("/api/v1/loan/eligibility", headers=auth)
        flow.record(
            title="Loan eligibility (all holdings)",
            method="GET",
            path="/api/v1/loan/eligibility",
            request_body=None,
            status=r.status_code,
            response_body=_json(r),
            headers={"Authorization": "Bearer <access_token>"},
        )
        if r.status_code != 200:
            _write(flow, success=False, app_email=app_email)
            return 1

        holding_ids = []
        if isinstance(collateral, dict):
            for asset in collateral.get("assets") or []:
                hid = asset.get("holding_id")
                if hid is not None:
                    holding_ids.append(hid)
        selected_body = {
            "holding_ids": holding_ids[:1] if holding_ids else [],
            "asset_ids": [],
        }
        if not selected_body["holding_ids"]:
            flow.record(
                title="Loan eligibility selected (skipped — no holdings)",
                method="POST",
                path="/api/v1/loan/eligibility/selected",
                request_body=selected_body,
                status=0,
                response_body={"skipped": True, "reason": "No holdings in collateral"},
                headers={"Authorization": "Bearer <access_token>", "Content-Type": "application/json"},
            )
        else:
            r = client.post(
                "/api/v1/loan/eligibility/selected",
                headers=auth,
                json=selected_body,
            )
            flow.record(
                title="Loan eligibility (selected holdings)",
                method="POST",
                path="/api/v1/loan/eligibility/selected",
                request_body=selected_body,
                status=r.status_code,
                response_body=_json(r),
                headers={"Authorization": "Bearer <access_token>", "Content-Type": "application/json"},
            )
            if r.status_code != 200:
                _write(flow, success=False, app_email=app_email)
                return 1

    orders_failed = getattr(flow, "_orders_failed", False)
    _write(flow, success=not orders_failed, app_email=app_email)
    return 1 if orders_failed else 0


def _write(flow: Flow, *, success: bool, app_email: str | None = None) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "recorded_at": flow.started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "success": success,
        "base_url": BASE,
        "provider_id": PROVIDER,
        "scope": (
            "Portfolio Aggregator HTTP endpoints for Fundvine "
            "(/api/v1 auth, connections, sync, portfolio, deposits, markets, orders, loan)."
        ),
        "app_email": app_email,
        "secrets_note": "passwords, tokens, and account numbers redacted/partially masked",
        "steps": flow.steps,
    }
    OUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)


if __name__ == "__main__":
    raise SystemExit(main())
