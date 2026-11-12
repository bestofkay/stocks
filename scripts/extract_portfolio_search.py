#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

js = Path("logs/atlas_ccore-0324.js").read_text(encoding="utf-8")
m = re.search(r'"app/PortfolioGridView":function\(\)\{', js)
chunk = js[m.start() : m.start() + 12000]
Path("logs/atlas_portfolio_gridview.js").write_text(chunk, encoding="utf-8")
# find searchHandler
idx = chunk.find("searchHandler")
print(chunk[idx : idx + 2500])
print("\n==== session bits ====")
for m in re.finditer(r".{0,40}session.{0,80}", chunk, re.I):
    print(m.group(0)[:140])
