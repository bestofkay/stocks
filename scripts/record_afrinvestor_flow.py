#!/usr/bin/env python3
"""Record a live Afrinvestor login → portfolio pull as a JSON transcript.

Credentials from env AFR_USER / AFR_PASS (never written in cleartext to the log).
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
os.environ.setdefault(
    "APP_SECRET_KEY",
    os.environ.get("APP_SECRET_KEY") or ("x" * 48),
)
os.environ.setdefault("PLAYWRIGHT_HEADLESS", "true")

from app.connectors.afrinvestor import api_paths  # noqa: E402
from app.connectors.afrinvestor.connector import AfrinvestConnector  # noqa: E402


def _jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


class Recorder:
    def __init__(self) -> None:
        self.steps: list[dict] = []
        self.started = datetime.now(timezone.utc).isoformat()

    def add(
        self,
        *,
        step: int,
        title: str,
        equivalent_api: str | None,
        method: str | None,
        request: dict,
        response: dict,
        notes: str | None = None,
    ) -> None:
        entry = {
            "step": step,
            "title": title,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "equivalent_http_api": equivalent_api,
            "http_method": method,
            "request": _jsonable(request),
            "response": _jsonable(response),
        }
        if notes:
            entry["notes"] = notes
        self.steps.append(entry)
        print(f"[{step}] {title}")


async def main() -> int:
    user = os.environ.get("AFR_USER", "").strip()
    password = os.environ.get("AFR_PASS", "")
    if not user or not password:
        print("Set AFR_USER and AFR_PASS", file=sys.stderr)
        return 1

    rec = Recorder()
    connector = AfrinvestConnector()
    n = 0

    try:
        # --- Step: provider metadata (local, no network) ---
        n += 1
        meta = connector.metadata()
        rec.add(
            step=n,
            title="Inspect Afrinvestor provider metadata",
            equivalent_api="GET /api/v1/providers/afrinvestor",
            method="GET",
            request={"path": "/api/v1/providers/afrinvestor"},
            response=meta.model_dump(mode="json"),
            notes="Shows required credential fields and that connector is operational.",
        )

        # --- Step: create connection (logical) ---
        n += 1
        rec.add(
            step=n,
            title="Create broker connection (logical record of what API would receive)",
            equivalent_api="POST /api/v1/connections",
            method="POST",
            request={
                "path": "/api/v1/connections",
                "headers": {"Authorization": "Bearer <app_access_token>", "Content-Type": "application/json"},
                "body": {
                    "provider_id": "afrinvestor",
                    "display_name": "Afrinvestor Main",
                    "credentials": {
                        "username": user,
                        "password": "[REDACTED]",
                    },
                    "options": {},
                },
            },
            response={
                "id": "(assigned-by-api-when-MySQL-running)",
                "provider_id": "afrinvestor",
                "display_name": "Afrinvestor Main",
                "status": "configured",
                "has_credentials": True,
                "note": "Password is encrypted at rest; never returned by API.",
            },
            notes="MySQL was not required for this live connector recording. Secrets redacted in this file.",
        )

        # --- Step: sync start (logical) ---
        n += 1
        rec.add(
            step=n,
            title="Start synchronization (logical)",
            equivalent_api="POST /api/v1/connections/{connection_id}/sync",
            method="POST",
            request={
                "path": "/api/v1/connections/{connection_id}/sync",
                "headers": {"Authorization": "Bearer <app_access_token>"},
            },
            response={
                "sync_id": "(assigned-by-api)",
                "status": "queued",
                "poll_url": "/api/v1/syncs/{sync_id}",
                "http_status": 202,
            },
            notes="Below steps are what the sync job actually executes via the connector.",
        )

        # --- LIVE: authenticate ---
        n += 1
        auth_req = {
            "credentials": {"username": user, "password": "[REDACTED]"},
            "login_url": "https://afrinvestor.com/auth",
            "actions": [
                "open_browser_chromium_headless",
                "goto_login_url",
                "dismiss_cookie_banner_if_present",
                "fill_input[type=email]",
                "fill_input[type=password]",
                "click_button_Sign_in",
                "wait_until_left_/auth_or_success_indicators",
            ],
        }
        auth = await connector.authenticate({"username": user, "password": password})
        page_url = connector._page.url if connector._page else None
        rec.add(
            step=n,
            title="LIVE connector.authenticate (Afrinvestor login)",
            equivalent_api="(internal) sync step: authenticate",
            method=None,
            request=auth_req,
            response={
                "success": auth.success,
                "status": auth.status,
                "message": auth.message,
                "mfa_challenge": auth.mfa_challenge,
                "page_url_after_login": page_url,
            },
        )
        if not auth.success:
            raise RuntimeError(f"Auth failed: {auth.status} {auth.message}")

        # --- LIVE: accounts ---
        n += 1
        accounts = await connector.get_accounts()
        rec.add(
            step=n,
            title="LIVE connector.get_accounts",
            equivalent_api="(internal) GET https://afrinvestor.com/api/all/portfolio/list",
            method="GET",
            request={
                "url": api_paths.PORTFOLIO_LIST,
                "auth": "browser_session_cookie_from_login",
            },
            response={
                "accounts": [a.model_dump(mode="json") for a in accounts],
                "count": len(accounts),
            },
            notes="Maps to portfolio DB accounts; exposed later via GET /api/v1/portfolio/accounts",
        )

        # --- LIVE: holdings per account ---
        all_holdings = []
        for acct in accounts:
            n += 1
            holdings = await connector.get_holdings(acct)
            all_holdings.extend(holdings)
            rec.add(
                step=n,
                title=f"LIVE connector.get_holdings for account {acct.external_account_id}",
                equivalent_api=(
                    f"(internal) GET {api_paths.equity_holdings(acct.external_account_id)}"
                ),
                method="GET",
                request={
                    "url": api_paths.equity_holdings(acct.external_account_id),
                    "account": acct.model_dump(mode="json"),
                },
                response={
                    "holdings": [h.model_dump(mode="json") for h in holdings],
                    "count": len(holdings),
                },
                notes="Maps to GET /api/v1/portfolio/holdings after sync persists to MySQL",
            )

        # --- LIVE: cash ---
        cash_by_account = []
        for acct in accounts:
            n += 1
            cash = await connector.get_cash_balance(acct)
            cash_by_account.append(
                {"account_id": acct.external_account_id, "cash_balance": str(cash) if cash is not None else None}
            )
            rec.add(
                step=n,
                title=f"LIVE connector.get_cash_balance for {acct.external_account_id}",
                equivalent_api="(internal) GET https://afrinvestor.com/api/all/wallet",
                method="GET",
                request={"url": api_paths.WALLET},
                response={"cash_balance": str(cash) if cash is not None else None, "currency": "NGN"},
                notes="Maps to accounts[].cash_balance and portfolio/summary cash_value",
            )

        # --- LIVE: summary ---
        n += 1
        summary = await connector.get_portfolio_summary(accounts[0]) if accounts else None
        rec.add(
            step=n,
            title="LIVE connector.get_portfolio_summary",
            equivalent_api="(internal) GET https://afrinvestor.com/api/all/portfolio/nets",
            method="GET",
            request={"url": api_paths.PORTFOLIO_NETS},
            response=summary.model_dump(mode="json") if summary else None,
            notes="Maps to GET /api/v1/portfolio/summary after sync",
        )

        # --- Final aggregated portfolio view (what client would read) ---
        n += 1
        portfolio_holdings_response = []
        for h in all_holdings:
            d = h.model_dump(mode="json")
            d["provider_id"] = "afrinvestor"
            if accounts:
                d["account_name"] = accounts[0].account_name
            portfolio_holdings_response.append(d)

        by_ccy = {}
        for h in all_holdings:
            ccy = h.currency or "NGN"
            bucket = by_ccy.setdefault(
                ccy,
                {
                    "currency": ccy,
                    "total_market_value": Decimal("0"),
                    "total_cost_basis": Decimal("0"),
                    "unrealized_pnl": Decimal("0"),
                    "cash_value": None,
                    "holdings_count": 0,
                    "has_mv": False,
                    "has_cost": False,
                    "has_pnl": False,
                },
            )
            if h.market_value is not None:
                bucket["total_market_value"] += h.market_value
                bucket["has_mv"] = True
            if h.cost_basis is not None:
                bucket["total_cost_basis"] += h.cost_basis
                bucket["has_cost"] = True
            if h.unrealized_pnl is not None:
                bucket["unrealized_pnl"] += h.unrealized_pnl
                bucket["has_pnl"] = True
            bucket["holdings_count"] += 1
        if cash_by_account and cash_by_account[0]["cash_balance"] is not None:
            by_ccy.setdefault(
                "NGN",
                {
                    "currency": "NGN",
                    "total_market_value": Decimal("0"),
                    "total_cost_basis": Decimal("0"),
                    "unrealized_pnl": Decimal("0"),
                    "cash_value": None,
                    "holdings_count": 0,
                    "has_mv": False,
                    "has_cost": False,
                    "has_pnl": False,
                },
            )
            by_ccy["NGN"]["cash_value"] = Decimal(cash_by_account[0]["cash_balance"])

        summary_body = {
            "by_currency": [
                {
                    "currency": ccy,
                    "total_market_value": str(b["total_market_value"]) if b["has_mv"] else None,
                    "total_cost_basis": str(b["total_cost_basis"]) if b["has_cost"] else None,
                    "cash_value": str(b["cash_value"]) if b["cash_value"] is not None else None,
                    "unrealized_pnl": str(b["unrealized_pnl"]) if b["has_pnl"] else None,
                    "holdings_count": b["holdings_count"],
                }
                for ccy, b in sorted(by_ccy.items())
            ],
            "accounts_count": len(accounts),
            "holdings_count": len(all_holdings),
            "connections_count": 1,
            "broker_summary": summary.model_dump(mode="json") if summary else None,
            "note": "Assembled from live connector results; identical shape to GET /api/v1/portfolio/* after a real sync.",
        }

        rec.add(
            step=n,
            title="FINAL portfolio summary view (client-facing)",
            equivalent_api="GET /api/v1/portfolio/summary",
            method="GET",
            request={
                "path": "/api/v1/portfolio/summary",
                "headers": {"Authorization": "Bearer <app_access_token>"},
            },
            response=summary_body,
        )

        n += 1
        rec.add(
            step=n,
            title="FINAL portfolio accounts view (client-facing)",
            equivalent_api="GET /api/v1/portfolio/accounts",
            method="GET",
            request={
                "path": "/api/v1/portfolio/accounts",
                "headers": {"Authorization": "Bearer <app_access_token>"},
            },
            response=[
                {
                    **a.model_dump(mode="json"),
                    "provider_id": "afrinvestor",
                    "cash_balance": next(
                        (c["cash_balance"] for c in cash_by_account if c["account_id"] == a.external_account_id),
                        None,
                    ),
                }
                for a in accounts
            ],
        )

        n += 1
        rec.add(
            step=n,
            title="FINAL portfolio holdings view (client-facing)",
            equivalent_api="GET /api/v1/portfolio/holdings",
            method="GET",
            request={
                "path": "/api/v1/portfolio/holdings?provider=afrinvestor",
                "headers": {"Authorization": "Bearer <app_access_token>"},
            },
            response=portfolio_holdings_response,
        )

        # --- disconnect ---
        n += 1
        await connector.disconnect()
        rec.add(
            step=n,
            title="LIVE connector.disconnect (browser cleanup)",
            equivalent_api="(internal) end of sync",
            method=None,
            request={"action": "close_browser_context_and_clear_session"},
            response={"disconnected": True},
        )

    except Exception as exc:
        n += 1
        rec.add(
            step=n,
            title="ERROR",
            equivalent_api=None,
            method=None,
            request={},
            response={"error_type": type(exc).__name__, "error": str(exc)[:500]},
        )
        try:
            await connector.disconnect()
        except Exception:
            pass
        out = {
            "recorded_at": rec.started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "success": False,
            "provider": "afrinvestor",
            "username": user,
            "password": "[REDACTED]",
            "steps": rec.steps,
        }
        path = ROOT / "logs" / "afrinvestor_full_flow.json"
        path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print("Wrote", path)
        return 1

    out = {
        "recorded_at": rec.started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "success": True,
        "provider": "afrinvestor",
        "username": user,
        "password": "[REDACTED]",
        "how_to_read": (
            "Each step shows request + response. Steps marked LIVE actually hit Afrinvestor. "
            "Steps marked FINAL show the JSON shape you get from portfolio endpoints after sync. "
            "App login (/api/v1/auth/*) needs MySQL; this transcript focuses on broker connector + portfolio output."
        ),
        "steps": rec.steps,
    }
    path = ROOT / "logs" / "afrinvestor_full_flow.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("Wrote", path)
    print("steps=", len(rec.steps), "success=", True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
