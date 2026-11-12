#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

js = Path("logs/atlas_ccore-0324.js").read_text(encoding="utf-8")
m = re.search(r'"common/util/RequestBroker":function\(\)\{', js)
print("start", m.start() if m else None)
if not m:
    raise SystemExit(1)
# find next module boundary roughly
start = m.start()
nxt = js.find('"},"', start + 10)
# better: find next `","common/` or similar module key after a long stretch
end = start + 8000
chunk = js[start:end]
Path("logs/atlas_requestbroker_chunk.js").write_text(chunk, encoding="utf-8")
print(chunk[:6000])

print("\n==== triples ====")
triples = set()
for m in re.finditer(r"\{[^{}]{0,300}\}", js):
    chunk = m.group(0)
    if "svc" not in chunk:
        continue
    s = re.search(r"""svc\s*:\s*["']([A-Za-z0-9_]+)["']""", chunk)
    i = re.search(r"""inf\s*:\s*["']([A-Za-z0-9_]+)["']""", chunk)
    a = re.search(r"""actn\s*:\s*["']([A-Za-z0-9_]+)["']""", chunk)
    if s and (i or a):
        triples.add((s.group(1), i.group(1) if i else "", a.group(1) if a else ""))
for t in sorted(triples):
    print(t)
