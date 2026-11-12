#!/usr/bin/env python3
"""Scan Atlas ccore JS for endpoint-like strings."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
js = (ROOT / "logs" / "atlas_ccore-0324.js").read_text(encoding="utf-8")
out: dict = {}

paths = set(re.findall(r"""["'](/?(?:folio|x|wa|customers|trader|api)[^"']{0,160})["']""", js, re.I))
paths |= set(re.findall(r"""["']((?:folio|x|wa)/[^"']{1,160})["']""", js, re.I))
paths |= set(re.findall(r"""["']([^"']*(?:Servlet|service|Service|Action)[^"']*)["']""", js))
# Relative command tokens often used by Folio: "cmd=..."
cmds = set(re.findall(r"""["']([^"']*(?:cmd|Cmd)=[^"']+)["']""", js))
# Bare service names
services = set(
    re.findall(
        r"""["']([A-Za-z][A-Za-z0-9_]*(?:Service|Servlet|Controller|Facade|Handler))["']""",
        js,
    )
)

out["paths"] = sorted(p for p in paths if 2 < len(p) < 160)[:500]
out["cmds"] = sorted(cmds)[:200]
out["services"] = sorted(services)[:200]

# Context around folio/
contexts = []
for m in re.finditer(r".{0,50}folio/.{0,80}", js, re.I):
    contexts.append(m.group(0))
    if len(contexts) >= 100:
        break
out["folio_contexts"] = contexts

# Context around likely data verbs
verb_ctx = []
for m in re.finditer(
    r".{0,40}(?:getPortfolio|getHoldings|getCash|getOrders|getAccounts|getBalance|placeOrder|deposit|statement|securities|marketData).{0,80}",
    js,
    re.I,
):
    verb_ctx.append(m.group(0))
    if len(verb_ctx) >= 100:
        break
out["verb_contexts"] = verb_ctx

# URL builders
for pat in [
    r"url\s*[:=]\s*['\"][^'\"]+['\"]",
    r"target\s*[:=]\s*['\"][^'\"]+['\"]",
    r"service\s*[:=]\s*['\"][^'\"]+['\"]",
    r"endpoint\s*[:=]\s*['\"][^'\"]+['\"]",
]:
    out[pat] = re.findall(pat, js, re.I)[:80]

# Keyword counts + sample
kw = {}
for key in [
    "portfolio",
    "holding",
    "account",
    "order",
    "cash",
    "deposit",
    "balance",
    "security",
    "market",
    "statement",
    "position",
    "fund",
    "watchlist",
    "customer",
    "/x/",
    "csax",
    "Request(",
]:
    idxs = [m.start() for m in re.finditer(re.escape(key), js, re.I)]
    samples = [js[max(0, i - 60) : i + 100] for i in idxs[:3]]
    kw[key] = {"count": len(idxs), "samples": samples}
out["keywords"] = kw

(ROOT / "logs" / "atlas_ccore_scan.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print("paths", len(out["paths"]))
for p in out["paths"][:80]:
    print(" ", p)
print("services", out["services"][:40])
print("cmds", out["cmds"][:40])
print("verb_contexts", len(out["verb_contexts"]))
for v in out["verb_contexts"][:30]:
    print(" V", v)
print("Wrote logs/atlas_ccore_scan.json")
