#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

p = Path(r"C:\Users\HELLO\.cursor\browser-logs\cdp-response-Runtime.evaluate-2026-11-12T12-17-59-588Z.json")
raw = json.loads(p.read_text(encoding="utf-8"))


def unwrap(obj):
    while isinstance(obj, dict) and "value" in obj and "type" in obj:
        obj = obj["value"]
    if isinstance(obj, dict) and "result" in obj:
        return unwrap(obj["result"])
    return obj


val = unwrap(raw)
results = val.get("results") if isinstance(val, dict) else []
print("meta", {k: val.get(k) for k in ("acct", "email", "user", "id")})
print("n", len(results))
summary = []
for r in results:
    payload = r.get("payload")
    resp = r.get("resp")
    timed = r.get("timedOut")
    info = {
        "payload": payload,
        "timedOut": timed,
        "respType": type(resp).__name__,
    }
    if isinstance(resp, list):
        info["len"] = len(resp)
        info["sample"] = resp[:2]
    elif isinstance(resp, dict):
        info["keys"] = list(resp.keys())[:30]
        # compact large nested
        compact = {}
        for k, v in list(resp.items())[:20]:
            if isinstance(v, list):
                compact[k] = {"len": len(v), "sample": v[:2]}
            elif isinstance(v, dict):
                compact[k] = {kk: vv for kk, vv in list(v.items())[:15]}
            else:
                compact[k] = v
        info["resp"] = compact
    else:
        info["resp"] = resp
    summary.append(info)
    print("---", payload)
    print(json.dumps(info, indent=2, default=str)[:1200])

Path("logs/atlas_holdings_probe_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
print("wrote summary")
