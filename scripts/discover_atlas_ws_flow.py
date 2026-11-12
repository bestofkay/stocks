#!/usr/bin/env python3
"""Login to Atlas CitiTrader and probe WebSocket services."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import httpx
import websockets

BASE = "https://app.atlassportfolios.com"
WS_URL = "wss://app.atlassportfolios.com/cftrader"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "atlas_ws_flow.json"


def hex_hlp85(password: str) -> str:
    h1 = hashlib.sha512(password.encode("utf-8")).hexdigest()
    return hashlib.sha512(h1.encode("utf-8")).hexdigest()


async def login(user: str, password: str) -> tuple[dict[str, str], dict]:
    cookies: dict[str, str] = {}
    settings: dict = {}
    async with httpx.AsyncClient(
        base_url=BASE,
        timeout=60.0,
        follow_redirects=True,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Origin": BASE,
            "Referer": f"{BASE}/customers/",
        },
    ) as client:
        await client.get("/customers/")
        for attempt in range(6):
            v = await client.post(
                "/folio/authCustomer?a=v",
                content=f"un={user}",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            auth = await client.post(
                "/folio/authCustomer",
                content=f"un={user}&pw={hex_hlp85(password)}",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            print(f"login {attempt+1}: {auth.text!r}", flush=True)
            if auth.text.startswith("OK||OK"):
                break
            if "logged in already" in auth.text.lower():
                # try override kick then wait
                await client.post(
                    "/folio/authCustomer?a=o",
                    content=f"un={user}&pw={hex_hlp85(password)}",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                await asyncio.sleep(20)
                continue
            raise RuntimeError(f"login failed: {auth.text}")
        else:
            raise RuntimeError("login failed after retries")

        cookies = {c.name: c.value for c in client.cookies.jar}
        wa = await client.get("/customers/wa/")
        html = wa.text
        (ROOT / "logs" / "atlas_wa.html").write_text(html, encoding="utf-8")
        # parse settings object loosely
        m = re.search(r"settings\s*=\s*\{(.*?)\};", html, re.S)
        if m:
            block = m.group(0)
            for key in ("id", "nonce", "user", "email", "contextPath", "appName"):
                mm = re.search(rf"{key}\s*:\s*\"([^\"]*)\"|{key}\s*:\s*(\d+)|{key}\s*:\s*(true|false)", block)
                if mm:
                    settings[key] = next(g for g in mm.groups() if g is not None)
            mm = re.search(r"tradeAccounts\s*:\s*\[([^\]]*)\]", block)
            if mm:
                settings["tradeAccounts"] = [int(x) for x in re.findall(r"\d+", mm.group(1))]
        print("settings", settings, "cookies", list(cookies), flush=True)
        if "loginContent" in html:
            raise RuntimeError("WA page still login form")
    return cookies, settings


async def ws_ask(ws, payload: dict, *, timeout: float = 8.0) -> dict:
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    def_id = payload["defId"]

    async def reader():
        while True:
            raw = await ws.recv()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("type") == "defReq" and msg.get("defId") == def_id:
                if not fut.done():
                    fut.set_result(msg)
                return

    task = asyncio.create_task(reader())
    await ws.send(json.dumps(payload))
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        task.cancel()
        return {"timedOut": True, "payload": payload}
    finally:
        if not task.done():
            task.cancel()


async def main() -> int:
    user = os.environ.get("ATLAS_USER", "").strip()
    password = os.environ.get("ATLAS_PASS", "")
    if not user or not password:
        print("Set ATLAS_USER / ATLAS_PASS", file=sys.stderr)
        return 1

    cookies, settings = await login(user, password)
    user_id = int(settings.get("id") or 0)
    accts = settings.get("tradeAccounts") or []
    acct = accts[0] if accts else None
    if not user_id:
        raise RuntimeError("missing settings.id")

    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    url = f"{WS_URL}?id={user_id}"
    print("ws connect", url, flush=True)

    results = []
    async with websockets.connect(
        url,
        additional_headers={
            "Cookie": cookie_header,
            "Origin": BASE,
            "User-Agent": "Mozilla/5.0",
        },
        max_size=20_000_000,
    ) as ws:
        def_id = 1

        async def ask(payload: dict, timeout: float = 10.0):
            nonlocal def_id
            body = {**payload, "defId": def_id}
            def_id += 1
            print(">>", {k: body[k] for k in body if k != "defId"}, flush=True)
            msg = await ws_ask(ws, body, timeout=timeout)
            if msg.get("timedOut"):
                print("<< TIMEOUT", flush=True)
                results.append({"payload": payload, "timedOut": True})
                return None
            resp = msg.get("resp")
            summary = {
                "payload": payload,
                "keys": list(msg.keys()),
                "total": msg.get("total"),
                "summary": msg.get("summary"),
                "respType": "list" if isinstance(resp, list) else type(resp).__name__,
                "len": len(resp) if isinstance(resp, list) else None,
                "sample": (resp[:3] if isinstance(resp, list) else resp),
            }
            # trim large samples
            if isinstance(resp, list) and len(resp) > 5:
                summary["sample"] = resp[:3]
                summary["len"] = len(resp)
            print("<<", summary["respType"], summary.get("len"), str(summary.get("sample"))[:200], flush=True)
            results.append(summary)
            return msg

        await ask({"svc": "ftwsdshbrds", "inf": "all", "actn": "q"})
        await ask({"svc": "ftwsdshbrds", "inf": "acct", "actn": "r"})
        await ask({"svc": "ftwsdshbrds", "inf": "pf", "actn": "r"})
        await ask({"svc": "ftwsexchs", "inf": "slct", "actn": "r"})
        await ask({"svc": "ftwsmds", "inf": "mde", "actn": "q"}, timeout=15)
        # holdings / orders grid style
        await ask({"svc": "ftwspfs", "actn": "q", "inf": "lst", "query": {}, "start": 0, "count": 0}, timeout=15)
        if acct is not None:
            await ask(
                {"svc": "ftwspfs", "actn": "q", "inf": "lst", "query": {"acct": acct}, "start": 0, "count": 100},
                timeout=15,
            )
            await ask(
                {"svc": "ftwsoms", "actn": "q", "inf": "lst", "query": {"acct": acct}, "start": 0, "count": 100},
                timeout=15,
            )
            await ask(
                {"svc": "ftwsexecs", "actn": "q", "inf": "lst", "query": {"acct": acct}, "start": 0, "count": 100},
                timeout=15,
            )
        await ask({"svc": "ftwsaccts", "actn": "q", "inf": "lst", "query": {}, "start": 0, "count": 100}, timeout=15)
        await ask({"svc": "ftwscss", "inf": "ssn", "actn": "r"})
        # webpay config if any
        await ask({"svc": "ftwswpcs", "inf": "cfg", "actn": "r"})

    OUT.write_text(
        json.dumps({"settings": settings, "cookie_names": list(cookies), "results": results}, indent=2, default=str),
        encoding="utf-8",
    )
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
