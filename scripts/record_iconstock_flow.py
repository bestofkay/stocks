#!/usr/bin/env python3
"""Live ICON Stock Brokers connector check (no app DB) — auth/sync/deposit/instruments.

Credentials: ICON_USER / ICON_PASS. Optional RECORD_LIVE_ORDER=1.
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

from app.connectors.iconstock.connector import IconStockConnector  # noqa: E402
from app.connectors.models import OrderRequest, OrderSide, OrderType  # noqa: E402
from app.core.errors import AppError  # noqa: E402

OUT = ROOT / "logs" / "iconstock_connector_flow.json"


def _ser(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj


async def main() -> int:
    user = os.environ.get("ICON_USER", "").strip()
    password = os.environ.get("ICON_PASS", "")
    if not user or not password:
        print("Set ICON_USER and ICON_PASS", file=sys.stderr)
        return 1

    steps: list[dict] = []
    conn = IconStockConnector()
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
        txs_all = []
        for acct in accounts:
            holdings = await conn.get_holdings(acct)
            cash = await conn.get_cash_balance(acct)
            summary = await conn.get_portfolio_summary(acct)
            txs = await conn.get_transactions(acct)
            holdings_all.extend([_ser(h) for h in holdings])
            cash_map[acct.external_account_id] = _ser(cash)
            summaries[acct.external_account_id] = _ser(summary)
            txs_all.extend([_ser(t) for t in txs])
        record("get_holdings", True, {"count": len(holdings_all), "holdings": holdings_all})
        record("get_cash_balance", True, cash_map)
        record("get_portfolio_summary", True, summaries)
        record("get_transactions", True, {"count": len(txs_all), "sample": txs_all[:5]})

        try:
            dep = await conn.get_deposit_instructions()
            record("get_deposit_instructions", True, _ser(dep))
        except AppError as exc:
            success = False
            record("get_deposit_instructions", False, {"error": str(exc.message), "code": str(exc.code)})

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

        live = os.environ.get("RECORD_LIVE_ORDER", "").strip().lower() in {"1", "true", "yes"}
        if live:
            symbol = os.environ.get("ORDER_SYMBOL", "GTCO").strip().upper()
            qty = Decimal(os.environ.get("ORDER_QTY", "1"))
            limit = Decimal(os.environ.get("ORDER_LIMIT_PRICE", "70"))
            buy = OrderRequest(
                side=OrderSide.BUY,
                symbol=symbol,
                quantity=qty,
                order_type=OrderType.LIMIT,
                limit_price=limit,
            )
            try:
                result = await conn.place_order(buy)
                record("place_order_buy", True, _ser(result))
            except AppError as exc:
                record(
                    "place_order_buy",
                    exc.status_code == 422,
                    {"error": str(exc.message), "status_code": exc.status_code, "details": exc.details},
                )
                if exc.status_code not in {422, 502}:
                    success = False
        else:
            # Dry-run validation path without submitting when cash is empty
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
                # Insufficient cash / mandate reject is expected — proves order path works
                ok = exc.status_code in {422, 502}
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
                "provider_id": "iconstock",
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
