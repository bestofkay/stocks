#!/usr/bin/env python3
"""Live check of the CardinalStone connector.

Credentials: CS_USER / CS_PASS (CS_OTP if 2FA is enabled).
Writes a redacted transcript to logs/cardinalstone_flow.json — tokens and
credentials are never written.

Read path always runs. Live orders (buy/sell) require RECORD_LIVE_ORDER=1:

    $env:CS_USER='you@email.com'; $env:CS_PASS='...'
    $env:RECORD_LIVE_ORDER='1'
    $env:ORDER_SYMBOL='GTCO'          # optional
    $env:ORDER_QTY='1'                # optional
    $env:ORDER_TYPE='market'          # or limit (+ ORDER_LIMIT_PRICE)
    python scripts\\record_cardinalstone_flow.py
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

from app.connectors.cardinalstone.connector import CardinalStoneConnector  # noqa: E402
from app.connectors.models import OrderRequest, OrderSide, OrderType  # noqa: E402
from app.connectors.cardinalstone import parser as cs_parser  # noqa: E402


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


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


async def main() -> int:
    user = os.environ.get("CS_USER", "").strip()
    password = os.environ.get("CS_PASS", "")
    if not user or not password:
        print("Set CS_USER and CS_PASS (and CS_OTP if 2FA is on)", file=sys.stderr)
        return 1
    creds = {"username": user, "password": password}
    if os.environ.get("CS_OTP"):
        creds["otp"] = os.environ["CS_OTP"].strip()

    out: dict = {"started_at": datetime.now(timezone.utc).isoformat(), "steps": []}
    failures = 0
    c = CardinalStoneConnector()

    async def step(name, coro, *, critical: bool = True):
        nonlocal failures
        try:
            result = await coro
            out["steps"].append({"step": name, "ok": True, "result": _jsonable(result)})
            print(f"PASS {name}")
            return result
        except Exception as exc:  # noqa: BLE001
            if critical:
                failures += 1
            out["steps"].append(
                {
                    "step": name,
                    "ok": False,
                    "critical": critical,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            print(f"{'FAIL' if critical else 'WARN'} {name}: {exc}")
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
        c = CardinalStoneConnector()
        resumed = await step("resume_stored_session", c.authenticate({}, session))
        if not resumed or not resumed.success:
            return 2

        accounts = await step("get_accounts", c.get_accounts()) or []
        holdings_by_acct: dict[str, list] = {}
        for acct in accounts:
            tag = acct.external_account_id
            holdings_by_acct[tag] = await step(f"get_holdings[{tag}]", c.get_holdings(acct)) or []
            await step(f"get_cash_balance[{tag}]", c.get_cash_balance(acct))
            await step(f"get_portfolio_summary[{tag}]", c.get_portfolio_summary(acct))
            await step(f"get_transactions[{tag}]", c.get_transactions(acct))
        await step("get_deposit_instructions", c.get_deposit_instructions())
        await step("list_markets", c.list_markets())
        inst = await step("list_instruments", c.list_instruments(per_page=5))
        if inst is not None:
            out["instrument_total"] = inst.total

        if _truthy("RECORD_LIVE_ORDER"):
            symbol = (os.environ.get("ORDER_SYMBOL") or "GTCO").strip().upper()
            qty = Decimal((os.environ.get("ORDER_QTY") or "1").strip())
            order_type_raw = (os.environ.get("ORDER_TYPE") or "market").strip().lower()
            order_type = OrderType.LIMIT if order_type_raw == "limit" else OrderType.MARKET
            limit_price = None
            if order_type == OrderType.LIMIT:
                limit_raw = (os.environ.get("ORDER_LIMIT_PRICE") or "").strip()
                if not limit_raw:
                    print("ORDER_LIMIT_PRICE required for limit orders", file=sys.stderr)
                    return 1
                limit_price = Decimal(limit_raw)

            buy = OrderRequest(
                side=OrderSide.BUY,
                symbol=symbol,
                quantity=qty,
                order_type=order_type,
                limit_price=limit_price,
                tif="DAY",
            )
            await step(f"place_order[BUY {symbol} x{qty}]", c.place_order(buy))

            # Sell the same symbol (after buy). If a different holding is large
            # enough and BUY was skipped/failed, fall back to that symbol.
            sell_symbol = symbol
            sell_qty = qty
            for tag, rows in holdings_by_acct.items():
                if tag == cs_parser.WALLET_ACCOUNT_ID:
                    continue
                for h in rows or []:
                    held_sym = (h.symbol or "").upper()
                    if held_sym == symbol and h.quantity and h.quantity >= qty:
                        sell_symbol, sell_qty = held_sym, qty
                        break
                else:
                    continue
                break

            sell = OrderRequest(
                side=OrderSide.SELL,
                symbol=sell_symbol,
                quantity=sell_qty,
                order_type=order_type,
                limit_price=limit_price,
                tif="DAY",
            )
            await step(
                f"place_order[SELL {sell_symbol} x{sell_qty}]",
                c.place_order(sell),
                critical=True,
            )
        else:
            out["steps"].append(
                {
                    "step": "place_order",
                    "ok": True,
                    "skipped": True,
                    "reason": "Set RECORD_LIVE_ORDER=1 to place buy+sell",
                }
            )
            print("SKIP place_order (set RECORD_LIVE_ORDER=1)")
    finally:
        await c.disconnect()

    out["failures"] = failures
    log = ROOT / "logs" / "cardinalstone_flow.json"
    log.parent.mkdir(exist_ok=True)
    log.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n{'ALL PASSED' if failures == 0 else f'{failures} step(s) failed'} — transcript: {log}")
    return 0 if failures == 0 else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
