#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

p = Path(r"C:\Users\HELLO\.cursor\browser-logs\cdp-response-Runtime.evaluate-2026-11-12T12-05-51-280Z.json")
raw = json.loads(p.read_text(encoding="utf-8"))


def unwrap(obj):
    while isinstance(obj, dict) and "value" in obj and set(obj.keys()) <= {
        "type",
        "value",
        "objectId",
        "description",
        "className",
        "subtype",
    }:
        obj = obj["value"]
    if isinstance(obj, dict) and "result" in obj:
        return unwrap(obj["result"])
    return obj


val = unwrap(raw)
out = Path("logs/atlas_ws_probe.json")
out.write_text(json.dumps(val, indent=2, default=str)[:2_000_000], encoding="utf-8")
print("acct", val.get("acct") if isinstance(val, dict) else None)
results = (val or {}).get("results") if isinstance(val, dict) else []
print("results", len(results))
for r in results:
    payload = r.get("payload")
    resp = r.get("resp")
    err = r.get("error") or r.get("reject")
    timed = r.get("timedOut")
    msgs = r.get("msgs") or []
    interesting = []
    for m in msgs:
        s = str(m.get("msg") or "")
        if "mdRT_" in s and "defId" not in s:
            continue
        interesting.append(s[:400])
    print("---", payload, "timed" if timed else "", "err" if err else "", "resp" if resp is not None else "")
    if resp is not None:
        print(" RESP", json.dumps(resp, default=str)[:500])
    if err:
        print(" ERR", err)
    for s in interesting[:3]:
        print(" MSG", s[:350])

# also dump leftover non-md
left = (val or {}).get("leftover") or []
print("leftover", len(left))
for m in left[-15:]:
    s = str(m.get("msg") or "")
    if "mdRT_" in s:
        continue
    print("L", m.get("dir"), s[:350])
