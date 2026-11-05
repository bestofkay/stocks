#!/usr/bin/env python3
"""Browse Morgan app using saved storage_state and capture JSON/API traffic."""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOGS = ROOT / "logs"
STATE = LOGS / "morgan_storage_state.json"
OUT = LOGS / "morgan_api_capture.json"

PAGES = [
    "https://www.morgancapitalgroup.com/dashboard",
    "https://www.morgancapitalgroup.com/portfolio",
    "https://www.morgancapitalgroup.com/wallet",
    "https://www.morgancapitalgroup.com/markets",
    "https://www.morgancapitalgroup.com/markets/equities",
    "https://www.morgancapitalgroup.com/trade",
    "https://www.morgancapitalgroup.com/orders",
    "https://www.morgancapitalgroup.com/fund",
    "https://www.morgancapitalgroup.com/deposit",
]


def redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(x in lk for x in ("password", "token", "authorization", "secret", "cookie", "session", "apikey", "refresh")):
                out[k] = "***"
            elif any(x in lk for x in ("account_number", "nuban", "accountnumber")):
                s = str(v)
                out[k] = (s[:2] + "***" + s[-2:]) if len(s) > 4 else "***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


async def main() -> int:
    if not STATE.exists():
        print("missing storage_state", STATE)
        return 2

    from playwright.async_api import async_playwright

    out = {"pages": [], "responses": [], "requests": [], "dom_extracts": {}}
    seen: set[str] = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False)
        context = await browser.new_context(storage_state=str(STATE))
        page = await context.new_page()

        async def on_req(req):
            u = req.url
            if "morgancapital" not in u and "supabase" not in u:
                return
            if req.method in {"POST", "PUT", "PATCH", "DELETE"} or "/api" in u or "supabase" in u:
                key = f"{req.method} {u}"
                if key in seen:
                    return
                seen.add(key)
                out["requests"].append({"method": req.method, "url": u, "post": (req.post_data or "")[:2000]})

        async def on_resp(resp):
            u = resp.url
            if "morgancapital" not in u and "supabase" not in u:
                return
            if any(x in u for x in ("/_next/static", ".css", ".woff", ".png", ".svg", ".jpg", ".webp")):
                return
            ct = resp.headers.get("content-type", "")
            interesting = (
                resp.request.method in {"POST", "PUT", "PATCH"}
                or "/api" in u
                or "supabase" in u
                or "json" in ct
                or "text/x-component" in ct
            )
            if not interesting and resp.request.method == "GET" and "rsc" not in ct:
                return
            key = f"{resp.status} {resp.request.method} {u}"
            if key in seen:
                return
            seen.add(key)
            text = ""
            parsed = None
            try:
                text = await resp.text()
                if text[:1] in "{[":
                    parsed = json.loads(text)
            except Exception:
                pass
            out["responses"].append(
                {
                    "status": resp.status,
                    "method": resp.request.method,
                    "url": u,
                    "ct": ct,
                    "body_preview": text[:5000],
                    "json": redact(parsed) if parsed is not None else None,
                }
            )

        page.on("request", on_req)
        page.on("response", on_resp)

        for url in PAGES:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(4000)
                # If bounced to sign-in, abort
                if "/sign-in" in page.url:
                    out["pages"].append({"url": url, "final": page.url, "auth": False})
                    break
                text = await page.inner_text("body")
                out["pages"].append(
                    {
                        "url": url,
                        "final": page.url,
                        "auth": True,
                        "text_preview": text[:1500],
                    }
                )
                # Heuristic extract for wallet VA
                if "wallet" in url or "fund" in url or "deposit" in url:
                    m = re.search(
                        r"(Wema|GTBank|Access|Zenith|Moniepoint|Providus)[^\n]{0,60}?(\d{10})",
                        text,
                        re.I,
                    )
                    if m:
                        out["dom_extracts"]["deposit"] = {
                            "bank": m.group(1),
                            "account_number_redacted": m.group(2)[:2] + "***" + m.group(2)[-2:],
                            "account_number": m.group(2),
                        }
                    # Also look for 10-digit alone near Virtual/Dedicated
                    m2 = re.search(r"(?:Virtual|Dedicated|NUBAN|Account)[^\d]{0,40}(\d{10})", text, re.I)
                    if m2 and "deposit" not in out["dom_extracts"]:
                        out["dom_extracts"]["deposit"] = {
                            "account_number": m2.group(1),
                            "account_number_redacted": m2.group(1)[:2] + "***" + m2.group(1)[-2:],
                        }
            except Exception as exc:
                out["pages"].append({"url": url, "error": str(exc)})

        # Click common labels to trigger more XHR
        for label in ("Fund wallet", "Fund", "Deposit", "Buy", "Equities", "Holdings"):
            try:
                loc = page.get_by_text(label, exact=False).first
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=2000)
                    await page.wait_for_timeout(2500)
            except Exception:
                pass

        # Try common API paths via page.evaluate fetch (same-origin session)
        probe_paths = [
            "/api/portfolio",
            "/api/portfolio/list",
            "/api/wallet",
            "/api/wallet/dedicated",
            "/api/markets",
            "/api/markets/equities",
            "/api/equities",
            "/api/orders",
            "/api/user",
            "/api/me",
            "/api/account",
            "/api/accounts",
            "/api/holdings",
            "/api/dashboard",
        ]
        out["probes"] = []
        for path in probe_paths:
            try:
                result = await page.evaluate(
                    """async (path) => {
                      const res = await fetch(path, { credentials: 'same-origin', headers: { 'Accept': 'application/json' }});
                      const ct = res.headers.get('content-type') || '';
                      const text = await res.text();
                      return { status: res.status, ct, text: text.slice(0, 3000) };
                    }""",
                    path,
                )
                out["probes"].append({"path": path, **result})
            except Exception as exc:
                out["probes"].append({"path": path, "error": str(exc)})

        # Also probe supabase from page if client exists
        sb = await page.evaluate(
            """async () => {
              const keys = Object.keys(window).filter(k => /supabase|createClient/i.test(k));
              let env = {};
              try {
                // next public env sometimes on window
                env = {
                  url: window.__SUPABASE_URL || null,
                  hasCreate: typeof createClient !== 'undefined'
                };
              } catch (e) {}
              return { keys, env, href: location.href };
            }"""
        )
        out["window_probe"] = sb

        await context.storage_state(path=str(STATE))
        await browser.close()

    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({
        "pages": len(out["pages"]),
        "responses": len(out["responses"]),
        "probes_ok": [p["path"] for p in out.get("probes", []) if p.get("status") == 200],
        "auth_pages": [p.get("final") for p in out["pages"] if p.get("auth")],
        "out": str(OUT),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
