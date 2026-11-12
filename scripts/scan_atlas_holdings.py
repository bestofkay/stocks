#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

js = Path("logs/atlas_ccore-0324.js").read_text(encoding="utf-8")
for key in [
    "Portfolio",
    "Holding",
    "Position",
    "pfsummary",
    "pftx",
    "stmt",
    "WebPay",
    "deposit",
    "fund",
    "TrackableWebsocketGridStore",
    "PortfolioGrid",
    "AccountGrid",
]:
    print(key, len(re.findall(re.escape(key), js, re.I)))

print("\n=== modules mentioning portfolio/holding/position ===")
mods = re.findall(r'"(common/app/[A-Za-z0-9_]+|app/[A-Za-z0-9_]+|fix/[A-Za-z0-9_]+)":function', js)
for m in sorted(set(mods)):
    if any(k in m.lower() for k in ("port", "hold", "pos", "acct", "order", "pay", "dep", "cash", "stmt", "report", "market", "sec")):
        print(m)

print("\n=== contexts with qty/value holdings-like ===")
for m in re.finditer(r".{0,60}(?:holdings|positions|portfolioDetails|pfTx|pftx|portfolioTx).{0,100}", js, re.I):
    print(m.group(0)[:180])
