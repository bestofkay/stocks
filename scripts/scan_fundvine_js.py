#!/usr/bin/env python3
"""Scan Fundvine static JS for endpoints."""

from __future__ import annotations

import re
from pathlib import Path

import httpx

BASE = "https://etrade.fundvinesecurities.com"
OUT = Path(__file__).resolve().parents[1] / "logs" / "fundvine_js_scan.txt"
PATHS = [
    "/Resources/js/custom.js",
    "/Resources/js/nayaoms_reset.js",
    "/Resources/js/globaltrybe_utility.js",
]


def main() -> None:
    lines: list[str] = []
    with httpx.Client(base_url=BASE, timeout=60.0) as client:
        # discover more js from homepage/login
        login = client.get("/Login.aspx").text
        scripts = re.findall(r'src="([^"]+\.js)"', login, re.I)
        paths = list(dict.fromkeys(PATHS + scripts))
        # also try listing common NaYa paths after a bare GET of site map candidates
        for extra in [
            "/Resources/js/nayaoms.js",
            "/Resources/js/nayaoms_main.js",
            "/Resources/js/dashboard.js",
            "/Resources/js/portfolio.js",
            "/Resources/js/trading.js",
            "/Resources/js/orders.js",
            "/Resources/js/site.js",
            "/Scripts/jquery.js",
        ]:
            if extra not in paths:
                paths.append(extra)

        for p in paths:
            r = client.get(p)
            lines.append(f"=== {p} status={r.status_code} len={len(r.content)} ===")
            if r.status_code >= 400 or not r.text:
                continue
            text = r.text
            urls = sorted(
                set(
                    re.findall(
                        r"""['"](/[^'"]+\.(?:aspx|ashx|asmx)[^'"]*|[^'"]*WebService[^'"]*|[^'"]*api/[^'"]*)['"]""",
                        text,
                        re.I,
                    )
                )
            )
            lines.append(f"urls({len(urls)}): {urls[:60]}")
            ajax = re.findall(r"""(?:url|URL)\s*[:=]\s*['"]([^'"]+)['"]""", text)
            if ajax:
                lines.append(f"ajax: {sorted(set(ajax))[:40]}")
            # PageMethod / ASMX patterns
            for m in re.finditer(r"""(?:PageMethods|\$\.ajax|fetch|axios)[^;]{0,200}""", text):
                snip = re.sub(r"\s+", " ", m.group(0))[:180]
                lines.append(f"call={snip}")

        # Also probe robots/common aspx names used by NaYa OMS
        candidates = [
            "/Default.aspx",
            "/Home.aspx",
            "/Dashboard.aspx",
            "/Portfolio.aspx",
            "/MyPortfolio.aspx",
            "/Holdings.aspx",
            "/Orders.aspx",
            "/Trade.aspx",
            "/Trading.aspx",
            "/Cash.aspx",
            "/Wallet.aspx",
            "/Accounts.aspx",
            "/InvestorHome.aspx",
            "/ClientHome.aspx",
            "/Main.aspx",
            "/Services/DataService.asmx",
            "/WebServices/DataService.asmx",
            "/api/",
            "/Handlers/Data.ashx",
        ]
        lines.append("=== probe paths (unauth) ===")
        for p in candidates:
            r = client.get(p, follow_redirects=False)
            loc = r.headers.get("location", "")
            lines.append(f"{p} -> {r.status_code} loc={loc[:80]}")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(OUT.read_text(encoding="utf-8")[:8000])


if __name__ == "__main__":
    main()
