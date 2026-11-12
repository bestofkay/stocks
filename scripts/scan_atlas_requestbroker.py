#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

js = Path("logs/atlas_ccore-0324.js").read_text(encoding="utf-8")

for name in [
    "RequestBroker",
    "TimeoutRequest",
    "WebSocket",
    "socket",
    "EventSource",
    "/x/",
    "servlet",
    "csax",
    "invoke",
    "xhr",
    "XMLHttpRequest",
    "dojo/request",
    "topic.publish",
]:
    print(f"{name}: {js.lower().count(name.lower())}")

idx = js.find("common/util/RequestBroker")
print("\n=== RequestBroker chunk ===")
print(js[idx : idx + 4000])

# find url templates with query
print("\n=== url-like ===")
for pat in re.findall(r"""["'][^"']{0,40}\?[^"']{0,80}["']""", js):
    if any(k in pat.lower() for k in ("folio", "cmd", "svc", "req", "data", "order", "acct", "port")):
        print(pat)

print("\n=== dojo/request usages ===")
for m in re.finditer(r".{0,30}dojo/request.{0,100}", js):
    print(m.group(0)[:130])
    if m.start() > 0 and m.start() > 500000:
        break

# Search for absolute/relative endpoints containing 'rpt' or 'stream' or 'md'
print("\n=== stream/md/rpt ===")
for m in re.finditer(r""".{0,20}(?:/md/|/rpt/|/stream|/push|/ws|wss://|ws://).{0,60}""", js, re.I):
    print(m.group(0)[:100])
