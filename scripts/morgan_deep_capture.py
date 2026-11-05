#!/usr/bin/env python3
"""Deep capture: wait for hydrated DOM + all network JSON after storage_state login."""
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
OUT = LOGS / "morgan_deep_capture.json"


def redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(x in lk for x in ("password", "token", "authorization", "secret", "cookie", "session", "apikey")):
                out[k] = "***"
            elif any(x in lk for x in ("account_number", "nuban")):
                s = str(v)
                out[k] = (s[:2] + "***" + s[-2:]) if len(s) > 4 else "***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


async def main() -> int:
    from playwright.async_api import async_playwright

    out = {"pages": {}, "network": [], "notes": []}
    seen: set[str] = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False)
        context = await browser.new_context(storage_state=str(STATE))
        page = await context.new_page()

        async def on_resp(resp):
            u = resp.url
            if not any(x in u for x in ("morgancapital", "supabase", "amazonaws")):
                return
            if any(x in u for x in ("/_next/static", ".css", ".woff", ".png", ".svg", ".jpg", ".webp", ".js?")):
                # keep only interesting js? skip
                if "/_next/static" in u:
                    return
            ct = resp.headers.get("content-type", "")
            method = resp.request.method
            try:
                text = await resp.text()
            except Exception:
                return
            interesting = (
                method != "GET"
                or "json" in ct
                or "supabase" in u
                or (text[:1] in "{[" and len(text) > 2)
                or ("₦" in text)
                or ("account" in text.lower() and "wema" in text.lower())
                or ("symbol" in text.lower() and "price" in text.lower())
            )
            # Also keep large RSC that aren't just Loading
            if "_rsc=" in u and "Loading" not in text and len(text) > 500:
                interesting = True
            if not interesting:
                return
            key = f"{resp.status}:{method}:{u}:{len(text)}"
            if key in seen:
                return
            seen.add(key)
            parsed = None
            if text[:1] in "{[":
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = None
            out["network"].append(
                {
                    "status": resp.status,
                    "method": method,
                    "url": u,
                    "ct": ct,
                    "body": text[:8000],
                    "json": redact(parsed) if parsed is not None else None,
                }
            )

        page.on("response", on_resp)

        targets = {
            "dashboard": "https://www.morgancapitalgroup.com/dashboard",
            "portfolio": "https://www.morgancapitalgroup.com/portfolio",
            "wallet": "https://www.morgancapitalgroup.com/wallet",
            "markets": "https://www.morgancapitalgroup.com/markets",
            "orders": "https://www.morgancapitalgroup.com/orders",
            "zenith": "https://www.morgancapitalgroup.com/markets/ZENITHBANK",
        }

        for name, url in targets.items():
            await page.goto(url, wait_until="domcontentloaded")
            # wait until Loading shimmer gone or timeout
            try:
                await page.wait_for_function(
                    """() => {
                      const t = document.body.innerText;
                      return t && !t.includes('aria-busy') && !/\\bLoading\\b/.test(t.slice(0,200))
                        || (t.includes('Wallet') || t.includes('Portfolio') || t.includes('Markets') || t.includes('Orders'))
                           && !t.includes('anim-shimmer');
                    }""",
                    timeout=20000,
                )
            except Exception:
                pass
            await page.wait_for_timeout(5000)
            text = await page.inner_text("body")
            html = await page.content()
            extract = {
                "url": page.url,
                "text": text[:4000],
                "links": [],
                "numbers_10digit": re.findall(r"\b\d{10}\b", text),
                "money": re.findall(r"₦[\d,]+(?:\.\d+)?", text),
                "symbols": re.findall(r"\b[A-Z]{3,12}\b", text),
            }
            # wallet-specific buttons
            if name == "wallet":
                for label in ("Fund", "Add money", "Virtual account", "Copy", "Account"):
                    try:
                        loc = page.get_by_text(label, exact=False).first
                        if await loc.count() and await loc.is_visible():
                            await loc.click(timeout=2000)
                            await page.wait_for_timeout(2500)
                            text2 = await page.inner_text("body")
                            extract["after_click_" + label] = text2[:2000]
                            extract["numbers_10digit"] += re.findall(r"\b\d{10}\b", text2)
                    except Exception as exc:
                        extract.setdefault("click_errors", []).append(f"{label}:{exc}")
            if name == "markets":
                # collect instrument rows
                try:
                    rows = await page.locator("table tr, [class*='row'], a[href*='/markets/']").all_text_contents()
                    extract["market_rows"] = [r.strip() for r in rows if r.strip()][:80]
                except Exception:
                    pass
                hrefs = await page.eval_on_selector_all(
                    "a[href*='/markets/']",
                    "els => els.map(e => e.getAttribute('href')).filter(Boolean)",
                )
                extract["market_hrefs"] = sorted(set(hrefs))[:100]
            if name == "zenith":
                for label in ("Buy", "Sell", "Place order"):
                    try:
                        loc = page.get_by_role("button", name=re.compile(label, re.I)).first
                        if await loc.count():
                            extract["has_button_" + label] = True
                    except Exception:
                        pass
            out["pages"][name] = extract

        out["actions_probe"] = {"skipped": True}
        await browser.close()

    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({
        "pages": list(out["pages"]),
        "network": len(out["network"]),
        "wallet_nums": out["pages"].get("wallet", {}).get("numbers_10digit"),
        "wallet_money": out["pages"].get("wallet", {}).get("money"),
        "market_hrefs": (out["pages"].get("markets", {}).get("market_hrefs") or [])[:15],
        "out": str(OUT),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
