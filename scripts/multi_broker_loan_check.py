#!/usr/bin/env python3
"""Connect Afrinvestor + CardinalStone for one user, sync, print loan totals.

Broker creds: BROKER_USER / BROKER_PASS (same for both providers).
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
OUT = ROOT / "logs" / "multi_broker_loan_check.json"
PROVIDERS = ("afrinvestor", "cardinalstone")


def _json(r: httpx.Response):
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}


def _wait_sync(client: httpx.Client, auth: dict, connection_id: int, timeout_sec: int = 600) -> dict:
    # Prefer latest sync on the connection (auto-sync on create may already be running)
    r = client.post(f"/api/v1/connections/{connection_id}/sync", headers=auth)
    body = _json(r)
    sync_id = None
    if r.status_code in (200, 202):
        sync_id = body.get("sync_id")
    elif r.status_code == 409:
        runs = _json(client.get(f"/api/v1/connections/{connection_id}/syncs", headers=auth))
        if isinstance(runs, list) and runs:
            sync_id = runs[0].get("id")
    if not sync_id:
        return {"ok": False, "error": "no sync_id", "start": body}

    deadline = time.time() + timeout_sec
    final = None
    while time.time() < deadline:
        final = _json(client.get(f"/api/v1/syncs/{sync_id}", headers=auth))
        status = final.get("status")
        print(f"  sync {sync_id} connection={connection_id} -> {status} step={final.get('current_step')}")
        if status in {"completed", "completed_with_warnings", "failed", "cancelled", "waiting_for_user"}:
            break
        time.sleep(3)
    return {"ok": (final or {}).get("status") in {"completed", "completed_with_warnings"}, "sync": final}


def main() -> int:
    user = (os.environ.get("BROKER_USER") or os.environ.get("CS_USER") or "").strip()
    password = os.environ.get("BROKER_PASS") or os.environ.get("CS_PASS") or ""
    if not user or not password:
        print("Set BROKER_USER and BROKER_PASS", file=sys.stderr)
        return 1

    app_email = os.environ.get("APP_EMAIL", f"multi+{uuid.uuid4().hex[:8]}@example.com")
    app_password = os.environ.get("APP_PASSWORD", "DemoPassphrase12!")
    timeout = float(os.environ.get("HTTP_TIMEOUT", "600"))

    report: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "app_email": app_email,
        "broker_user": user,
        "steps": [],
    }

    with httpx.Client(base_url=BASE, timeout=timeout) as client:
        r = client.post("/api/v1/auth/register", json={"email": app_email, "password": app_password})
        report["steps"].append({"register": r.status_code})
        if r.status_code not in (201, 409):
            print("register failed", r.status_code, _json(r))
            return 1

        r = client.post("/api/v1/auth/login", json={"email": app_email, "password": app_password})
        if r.status_code != 200:
            print("login failed", r.status_code, _json(r))
            return 1
        token = r.json()["access_token"]
        auth = {"Authorization": f"Bearer {token}"}

        connections: dict[str, int] = {}
        for provider in PROVIDERS:
            body = {
                "provider_id": provider,
                "display_name": f"{provider} Main",
                "credentials": {"username": user, "password": password},
            }
            r = client.post("/api/v1/connections", headers=auth, json=body)
            resp = _json(r)
            report["steps"].append({"create_connection": provider, "status": r.status_code, "body": resp})
            print(f"create {provider} -> {r.status_code}")
            if r.status_code != 201:
                OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
                return 1
            connections[provider] = resp["id"]
            # Give auto-sync a moment to start before next create
            time.sleep(1)

        sync_results = {}
        for provider, cid in connections.items():
            print(f"waiting sync for {provider} (connection {cid})...")
            # Afrinvestor can be slow (browser)
            sync_timeout = 600 if provider == "afrinvestor" else 180
            sync_results[provider] = _wait_sync(client, auth, cid, timeout_sec=sync_timeout)
            report["steps"].append({"sync": provider, **sync_results[provider]})
            if not sync_results[provider]["ok"]:
                print(f"WARN: {provider} sync not fully successful:", sync_results[provider])

        holdings = _json(client.get("/api/v1/portfolio/holdings", headers=auth))
        accounts = _json(client.get("/api/v1/portfolio/accounts", headers=auth))
        summary = _json(client.get("/api/v1/portfolio/summary", headers=auth))
        collateral = _json(client.get("/api/v1/loan/collateral", headers=auth))
        eligibility = _json(client.get("/api/v1/loan/eligibility", headers=auth))

        selected_ids = []
        if isinstance(collateral, dict):
            for a in collateral.get("assets") or []:
                if a.get("holding_id") is not None:
                    selected_ids.append(a["holding_id"])
        selected = None
        if selected_ids:
            r = client.post(
                "/api/v1/loan/eligibility/selected",
                headers=auth,
                json={"holding_ids": selected_ids},
            )
            selected = _json(r)

        report["connections"] = connections
        report["sync_results"] = {
            k: {"ok": v.get("ok"), "status": (v.get("sync") or {}).get("status")}
            for k, v in sync_results.items()
        }
        report["portfolio_summary"] = summary
        report["portfolio_holdings"] = holdings
        report["portfolio_accounts"] = accounts
        report["loan_collateral"] = collateral
        report["loan_eligibility"] = eligibility
        report["loan_eligibility_selected_all"] = selected
        report["finished_at"] = datetime.now(timezone.utc).isoformat()

        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

        print("\n=== PORTFOLIO HOLDINGS ===")
        if isinstance(holdings, list):
            for h in holdings:
                print(
                    f"  [{h.get('provider_id')}] {h.get('symbol')} "
                    f"qty={h.get('quantity')} value={h.get('market_value')} {h.get('currency')}"
                )
        print("\n=== LOAN COLLATERAL ===")
        if isinstance(collateral, dict):
            for a in collateral.get("assets") or []:
                print(
                    f"  [{a.get('provider_id')}] {a.get('symbol')} "
                    f"value={a.get('asset_value')} {a.get('currency')} id={a.get('asset_id')}"
                )
            print("  by_provider:", json.dumps(collateral.get("by_provider"), indent=2, default=str))
            print(
                f"  grand_total_asset_value={collateral.get('grand_total_asset_value')} "
                f"grand_total_eligible_loan={collateral.get('grand_total_eligible_loan')}"
            )
        print("\n=== LOAN ELIGIBILITY (ALL) ===")
        print(json.dumps(eligibility, indent=2, default=str))
        print("\nWrote", OUT)
        return 0 if all(v.get("ok") for v in sync_results.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
