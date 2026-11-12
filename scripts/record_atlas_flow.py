#!/usr/bin/env python3
"""Live Atlas connector check — auth/sync/deposit/instruments/orders.

Credentials: ATLAS_USER / ATLAS_PASS.
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

from app.connectors.atlas.connector import AtlasConnector  # noqa: E402
from app.connectors.models import OrderRequest, OrderSide, OrderType  # noqa: E402
from app.core.errors import AppError  # noqa: E402

OUT = ROOT / "logs" / "atlas_connector_flow.json"


def _ser(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj


async def main() -> int:
    user = os.environ.get("ATLAS_USER", "").strip()
    password = os.environ.get("ATLAS_PASS", "")
    if not user or not password:
        print("Set ATLAS_USER and ATLAS_PASS", file=sys.stderr)
        return 1

    steps: list[dict] = []
    conn = AtlasConnector()
    success = True

    def record(title: str, ok: bool, body):
        steps.append({"title": title, "ok": ok, "body": body})
        print(f"[{'OK' if ok else 'FAIL'}] {title}", flush=True)

    try:
        auth = await conn.authenticate({"username": user, "password": password})
        record("authenticate", auth.success, {"status": auth.status, "message": auth.message})
        if not auth.success:
            success = False
            raise SystemExit(1)

        accounts = await conn.get_accounts()
        record("get_accounts", True, {"count": len(accounts), "accounts": [_ser(a) for a in accounts]})

        holdings_all = []
        cash_map = {}
        summaries = {}
        for acct in accounts:
            holdings = await conn.get_holdings(acct)
            cash = await conn.get_cash_balance(acct)
            summary = await conn.get_portfolio_summary(acct)
            holdings_all.extend([_ser(h) for h in holdings])
            cash_map[acct.external_account_id] = _ser(cash)
            summaries[acct.external_account_id] = _ser(summary)
        record("get_holdings", True, {"count": len(holdings_all), "holdings": holdings_all})
        record("get_cash_balance", True, cash_map)
        record("get_portfolio_summary", True, summaries)

        try:
            dep = await conn.get_deposit_instructions()
            record("get_deposit_instructions", True, _ser(dep))
        except AppError as exc:
            success = False
            record("get_deposit_instructions", False, {"error": str(exc.message)})

        markets = await conn.list_markets()
        record("list_markets", True, [_ser(m) for m in markets])

        instruments = await conn.list_instruments(market_id="NGX", search="GTCO", page=1, per_page=5)
        record(
            "list_instruments",
            True,
            {
                "total": instruments.total,
                "page": instruments.page,
                "sample": [_ser(i) for i in instruments.instruments],
            },
        )

        try:
            buy = OrderRequest(
                side=OrderSide.BUY,
                symbol="GTCO",
                quantity=Decimal("1"),
                order_type=OrderType.LIMIT,
                limit_price=Decimal("70"),
            )
            result = await conn.place_order(buy)
            record("place_order_buy", True, _ser(result))
        except AppError as exc:
            ok = exc.status_code in {422, 502, 504}
            record(
                "place_order_buy",
                ok,
                {"error": str(exc.message), "status_code": exc.status_code, "details": exc.details},
            )
            if not ok:
                success = False

    except SystemExit:
        success = False
    except Exception as exc:
        success = False
        record("fatal", False, {"error": str(exc)})
        print("FATAL", exc, file=sys.stderr)
    finally:
        await conn.disconnect()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "success": success,
                "provider_id": "atlas",
                "steps": steps,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print("Wrote", OUT, "success=", success)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
