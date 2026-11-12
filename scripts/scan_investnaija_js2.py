#!/usr/bin/env python3
"""Deeper scan of InvestNaija main.js for order/deposit strings."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parents[1] / "logs" / "investnaija_js_scan2.json"
URL = "https://app.investnaija.com/main.11b3686bf10f5c65.js"


def ctx(text: str, idx: int, before: int = 160, after: int = 220) -> str:
    return text[max(0, idx - before) : min(len(text), idx + after)].replace("\n", " ")


def main() -> int:
    text = httpx.get(URL, timeout=120.0, headers={"User-Agent": "Mozilla/5.0"}).text
    print("len", len(text))
    needles = [
        "trade-order",
        "calculate-price",
        "order-terms",
        "wallet/balance",
        "vNuban",
        "virtualAccount",
        "x-secure-token",
        "uuid_token",
        "createOrder",
        "placeOrder",
        "limitPrice",
        "orderQty",
        "orderQuantity",
        "timeInForce",
        "FIX_TIME",
        "GOOD_TILL",
        "BUY",
        "SELL",
        "side:",
        "portfolioId",
        "securityId",
        "secId",
        "nuban",
        "topup",
        "fundWallet",
        "deposit",
    ]
    found: dict[str, list[str]] = {}
    for n in needles:
        idxs = [m.start() for m in re.finditer(re.escape(n), text, re.I)]
        print(n, len(idxs))
        found[n] = [ctx(text, i) for i in idxs[:8]]

    # extract quoted strings containing trades/ or transactions/
    qpaths = sorted(set(re.findall(r'["\']((?:trades|transactions|customers|investin|payments|auth)/[^"\']{1,120})["\']', text)))
    print("quoted paths", len(qpaths))
    for p in qpaths:
        print(" ", p)

    # also unquoted concatenations like "trades/"+ or `trades/`
    concat = sorted(set(re.findall(r'["\']([a-z]+/[a-zA-Z0-9_\-]+)["\']', text)))
    interesting = [p for p in concat if any(x in p for x in ("trade", "wallet", "order", "nuban", "fund", "port", "secur", "calc", "pay"))]
    print("interesting concat", len(interesting))
    for p in interesting[:100]:
        print(" ", p)

    OUT.write_text(
        json.dumps({"quoted_paths": qpaths, "interesting": interesting, "contexts": found}, indent=2),
        encoding="utf-8",
    )
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
