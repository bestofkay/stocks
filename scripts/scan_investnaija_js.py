#!/usr/bin/env python3
"""Scan InvestNaija Angular main bundle for API paths and order payload keys."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "investnaija_js_scan.json"
ORIGIN = "https://app.investnaija.com"

KEYWORDS = [
    "trade-order",
    "calculate-price",
    "virtualAccount",
    "vNuban",
    "nuban",
    "wallet/balance",
    "create",
    "orderType",
    "orderTerm",
    "side",
    "quantity",
    "limitPrice",
    "x-secure-token",
    "uuid_token",
    "portfolioHoldings",
    "availableCash",
]


def main() -> int:
    with httpx.Client(timeout=60.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
        html = client.get(ORIGIN + "/").text
        scripts = re.findall(r'src="([^"]+\.js)"', html)
        print("scripts", scripts)
        findings: dict[str, list[str]] = {"scripts": scripts, "paths": [], "snippets": []}
        for src in scripts:
            if src.startswith("http"):
                continue
            url = ORIGIN + "/" + src.lstrip("/")
            text = client.get(url).text
            print(src, "len", len(text))
            # API path-like strings
            paths = sorted(set(re.findall(r"/api/v1/[a-zA-Z0-9_\-./?=&]+", text)))
            findings["paths"].extend(paths)
            # relative service paths
            rel = sorted(set(re.findall(
                r"[\"'`]((?:auth|customers|trades|transactions|investin|payments|funding|wallet|bank)[a-zA-Z0-9_\-./?=]{2,80})[\"'`]",
                text,
            )))
            findings.setdefault("rel_paths", []).extend(rel)
            for kw in KEYWORDS:
                for m in re.finditer(re.escape(kw), text, re.I):
                    start = max(0, m.start() - 120)
                    end = min(len(text), m.end() + 180)
                    snippet = text[start:end].replace("\n", " ")
                    findings["snippets"].append({"kw": kw, "file": src, "snippet": snippet})
            # look specifically for create order body field names near trade-order
            for m in re.finditer(r"trade-order[^\"]{0,40}", text):
                start = max(0, m.start() - 200)
                end = min(len(text), m.end() + 400)
                findings.setdefault("trade_order_ctx", []).append(text[start:end].replace("\n", " ")[:500])

        findings["paths"] = sorted(set(findings["paths"]))
        findings["rel_paths"] = sorted(set(findings.get("rel_paths", [])))
        # keep snippet volume manageable
        findings["snippets"] = findings["snippets"][:300]
        findings["trade_order_ctx"] = findings.get("trade_order_ctx", [])[:40]
        OUT.write_text(json.dumps(findings, indent=2), encoding="utf-8")
        print("paths", len(findings["paths"]))
        for p in findings["paths"]:
            print(" ", p)
        print("rel", len(findings["rel_paths"]))
        for p in findings["rel_paths"][:80]:
            print(" ", p)
        print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
