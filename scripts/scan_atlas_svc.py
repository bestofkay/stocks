#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

js = Path("logs/atlas_ccore-0324.js").read_text(encoding="utf-8")
svcs = sorted(set(re.findall(r"""svc\s*:\s*["']([A-Za-z0-9_]+)["']""", js)))
acts = sorted(set(re.findall(r"""actn\s*:\s*["']([A-Za-z0-9_]+)["']""", js)))
infs = sorted(set(re.findall(r"""inf\s*:\s*["']([A-Za-z0-9_]+)["']""", js)))
print("SVCS", len(svcs))
for s in svcs:
    print(" ", s)
print("ACTNS", acts)
print("INFS", infs)

# contexts for portfolio-like services
for svc in svcs:
    if any(k in svc.lower() for k in ("port", "hold", "cash", "acct", "ord", "mkt", "sec", "dep", "bal", "pos", "stmt", "cust", "fund", "pay")):
        print(f"\n=== {svc} ===")
        for m in re.finditer(rf".{{0,80}}svc\s*:\s*[\"']{re.escape(svc)}[\"'].{{0,120}}", js):
            print(m.group(0)[:200])
