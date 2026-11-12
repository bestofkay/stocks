#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

js = Path("logs/atlas_ccore-0324.js").read_text(encoding="utf-8")
for mod in [
    "app/PortfolioGridView",
    "app/CardPortfolio",
    "app/WebPayGridView",
    "app/WebPayUI",
    "app/MyReportView",
    "common/util/TrackableWebsocketGridStore",
]:
    m = re.search(rf'"{re.escape(mod)}":function\(\)\{{', js)
    print(f"\n===== {mod} @ {m.start() if m else None} =====")
    if not m:
        continue
    chunk = js[m.start() : m.start() + 3500]
    # highlight svc/inf
    print(chunk[:3200])
    print("svc hits:", re.findall(r"""svc\s*:\s*["'][^"']+["']""", chunk))
    print("inf hits:", re.findall(r"""inf\s*:\s*["'][^"']+["']""", chunk))
