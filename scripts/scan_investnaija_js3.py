#!/usr/bin/env python3
"""Hunt obfuscated / concatenated API strings in InvestNaija bundles."""

from __future__ import annotations

import re
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parents[1] / "logs" / "investnaija_js_scan3.txt"
ORIGIN = "https://app.investnaija.com"


def main() -> int:
    html = httpx.get(ORIGIN + "/", timeout=60).text
    scripts = re.findall(r'src="([^"]+\.js)"', html)
    lines: list[str] = []
    for src in scripts:
        if src.startswith("http"):
            continue
        url = ORIGIN + "/" + src.lstrip("/")
        text = httpx.get(url, timeout=120).text
        lines.append(f"=== {src} len={len(text)}")
        # unicode escapes
        for m in re.finditer(r"(?:\\u[0-9a-fA-F]{4}){4,}", text):
            try:
                decoded = bytes(m.group(), "utf-8").decode("unicode_escape")
            except Exception:
                continue
            if any(k in decoded.lower() for k in ("trade", "order", "price", "wallet", "nuban", "quant")):
                lines.append(f"unicode: {decoded[:200]}")
        # hex escapes
        for pat in [
            r"calculate.{0,5}price",
            r"quantity.{0,5}Requested",
            r"priceType",
            r"orderType",
            r"trade.order",
            r"TradeIN",
            r"tradein",
            r"Trade In",
            r"dashboard/[a-zA-Z0-9_\-/]+",
            r"limitPrice",
            r"orderPrice",
            r"requestedPrice",
            r"fixPrice",
        ]:
            ms = list(re.finditer(pat, text, re.I))
            lines.append(f"{pat}: {len(ms)}")
            for m in ms[:5]:
                i = m.start()
                lines.append("  " + text[max(0, i - 80) : i + 120].replace("\n", " "))

        # route paths like path:'xxx'
        routes = sorted(set(re.findall(r"path\s*:\s*['\"]([a-zA-Z0-9_\-/]+)['\"]", text)))
        trade_routes = [r for r in routes if any(x in r.lower() for x in ("trade", "stock", "market", "order", "invest", "wallet", "equity"))]
        lines.append(f"routes_total={len(routes)} interesting={trade_routes[:50]}")

        # Angular router loadChildren path fragments
        frags = sorted(set(re.findall(r"['\"]((?:dashboard|trade|market|order|wallet|invest)[^'\"]{0,60})['\"]", text, re.I)))
        lines.append(f"frags={frags[:80]}")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[-40:]))
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
