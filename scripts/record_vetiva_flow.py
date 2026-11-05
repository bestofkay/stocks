#!/usr/bin/env python3
"""Live read-only check of the Vetiva connector (no orders placed).

Note: Vetiva allows one active session per user, so this signs the web portal out.

Credentials come from env VT_USER / VT_PASS (and VT_OTP — the emailed code — if 2FA is enabled).
Writes a redacted transcript to logs/vetiva_flow.json — tokens and
credentials are never written. Exit code 0 means every read step succeeded.

    $env:VT_USER='you@email.com'; $env:VT_PASS='...'
    python scripts\\record_vetiva_flow.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("DATABASE_URL", "mysql+pymysql://u:p@127.0.0.1:3306/portfolio")
os.environ.setdefault(
    "APP_ENCRYPTION_KEY",
    os.environ.get("APP_ENCRYPTION_KEY")
    or __import__("base64").b64encode(__import__("secrets").token_bytes(32)).decode(),
)
os.environ.setdefault("APP_SECRET_KEY", os.environ.get("APP_SECRET_KEY") or ("x" * 48))

from app.connectors.vetiva.connector import VetivaConnector  # noqa: E402


def _jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items() if k not in {"options_patch"}}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


async def main() -> int:
    user = os.environ.get("VT_USER", "").strip()
    password = os.environ.get("VT_PASS", "")
    if not user or not password:
        print("Set VT_USER and VT_PASS (and VT_OTP if 2FA is on)", file=sys.stderr)
        return 1
    creds = {"username": user, "password": password}
    if os.environ.get("VT_OTP"):
        creds["otp"] = os.environ["VT_OTP"].strip()

    out: dict = {"started_at": datetime.now(timezone.utc).isoformat(), "steps": []}
    failures = 0
    c = VetivaConnector()

    async def step(name, coro):
        nonlocal failures
        try:
            result = await coro
            out["steps"].append({"step": name, "ok": True, "result": _jsonable(result)})
            print(f"PASS {name}")
            return result
        except Exception as exc:  # noqa: BLE001
            failures += 1
            out["steps"].append({"step": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            print(f"FAIL {name}: {exc}")
            return None

    try:
        auth = await step("authenticate", c.authenticate(creds))
        creds.clear()
        if not auth or not auth.success:
            print(f"Auth status: {getattr(auth, 'status', None)} — {getattr(auth, 'message', None)}")
            return 2
        # Prove the stored-session path works too (what scheduled syncs use)
        session = auth.options_patch
        await c.disconnect()
        c = VetivaConnector()
        resumed = await step("resume_stored_session", c.authenticate({}, session))
        if not resumed or not resumed.success:
            return 2

        accounts = await step("get_accounts", c.get_accounts()) or []
        for acct in accounts:
            tag = acct.external_account_id
            await step(f"get_holdings[{tag}]", c.get_holdings(acct))
            await step(f"get_cash_balance[{tag}]", c.get_cash_balance(acct))
            await step(f"get_portfolio_summary[{tag}]", c.get_portfolio_summary(acct))
            await step(f"get_transactions[{tag}]", c.get_transactions(acct))
        await step("list_markets", c.list_markets())
        inst = await step("list_instruments", c.list_instruments(per_page=5))
        if inst is not None:
            out["instrument_total"] = inst.total
    finally:
        await c.disconnect()

    out["failures"] = failures
    log = ROOT / "logs" / "vetiva_flow.json"
    log.parent.mkdir(exist_ok=True)
    log.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n{'ALL PASSED' if failures == 0 else f'{failures} step(s) failed'} — transcript: {log}")
    return 0 if failures == 0 else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
