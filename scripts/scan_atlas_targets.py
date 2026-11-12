#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

js = Path("logs/atlas_ccore-0324.js").read_text(encoding="utf-8")
targets = sorted(set(re.findall(r"""target\s*:\s*["']([A-Za-z0-9_]+)["']""", js)))
print("targets", targets)

# WebsocketGridStore chunk
m = re.search(r'"common/util/WebsocketGridStore":function\(\)\{', js)
print("\nWebsocketGridStore", m.start() if m else None)
if m:
    print(js[m.start() : m.start() + 2500])

# contexts around ftwspfs
print("\n=== ftwspfs contexts ===")
for m in re.finditer(r".{0,80}ftwspfs.{0,120}", js):
    print(m.group(0))
