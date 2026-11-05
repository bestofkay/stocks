#!/usr/bin/env python3
"""Verify Morgan production session path: storage_state → headless scrape.

Uses ``logs/morgan_storage_state.json`` when present (no UC). Falls back to
UC bootstrap with MORGAN_USER / MORGAN_PASS when the state is missing/expired.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STATE = ROOT / "logs" / "morgan_storage_state.json"
OUT = ROOT / "logs" / "morgan_session_verify.json"


async def main() -> int:
    from app.connectors.morgan.connector import MorganCapitalConnector
    from app.connectors.morgan import session as morgan_session

    user = (os.environ.get("MORGAN_USER") or "").strip()
    password = os.environ.get("MORGAN_PASS") or ""
    report: dict = {"ok": False, "steps": []}

    connector = MorganCapitalConnector()
    try:
        options: dict = {"scrape_headless": True}
        if STATE.is_file():
            options["storage_state"] = json.loads(STATE.read_text(encoding="utf-8"))
            report["steps"].append({"step": "load_state", "path": str(STATE), "has_session": morgan_session.storage_state_has_session(options["storage_state"])})
        if not user or not password:
            if "storage_state" not in options:
                report["error"] = "Need MORGAN_USER/MORGAN_PASS or logs/morgan_storage_state.json"
                OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(json.dumps(report, indent=2))
                return 2
            # State-only path still needs placeholder creds for schema
            user = user or "session-reuse@local"
            password = password or "unused"

        auth = await connector.authenticate(
            {"username": user, "password": password},
            options,
        )
        report["steps"].append(
            {
                "step": "authenticate",
                "success": auth.success,
                "status": auth.status,
                "message": auth.message,
                "session_metadata": auth.session_metadata,
                "has_options_patch": bool(auth.options_patch),
                "patch_has_cookies": bool(
                    (auth.options_patch or {}).get("storage_state", {}).get("cookies")
                )
                if auth.options_patch
                else False,
            }
        )
        if not auth.success:
            report["error"] = auth.message
            OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, indent=2))
            return 1

        accounts = await connector.get_accounts()
        cash = await connector.get_cash_balance(accounts[0]) if accounts else None
        report["steps"].append(
            {
                "step": "scrape",
                "accounts": [a.model_dump(mode="json") for a in accounts],
                "cash": str(cash) if cash is not None else None,
            }
        )
        # Refresh local debug state from options_patch (encrypted path is API's job)
        if auth.options_patch and auth.options_patch.get("storage_state"):
            STATE.parent.mkdir(exist_ok=True)
            STATE.write_text(
                json.dumps(auth.options_patch["storage_state"], indent=2),
                encoding="utf-8",
            )
            report["steps"].append({"step": "wrote_debug_state", "path": str(STATE)})

        report["ok"] = True
        OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0
    finally:
        await connector.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
