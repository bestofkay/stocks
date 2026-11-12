#!/usr/bin/env python3
"""Probe Atlas portfolio list with session-aware queries."""

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
OUT = Path("logs/atlas_portfolio_query.json")


def hex_hlp85(password: str) -> str:
    h1 = hashlib.sha512(password.encode("utf-8")).hexdigest()
    return hashlib.sha512(h1.encode("utf-8")).hexdigest()


async def login(user: str, password: str):
    async with httpx.AsyncClient(
        base_url=BASE,
        timeout=60.0,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0", "Origin": BASE, "Referer": f"{BASE}/customers/"},
    ) as client:
        await client.get("/customers/")
        await client.post(
            "/folio/authCustomer?a=v",
            content=f"un={user}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        auth = await client.post(
            "/folio/authCustomer",
            content=f"un={user}&pw={hex_hlp85(password)}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        print("auth", auth.text, flush=True)
        if not auth.text.startswith("OK||OK"):
            if "logged in already" in auth.text.lower():
                await client.post(
                    "/folio/authCustomer?a=o",
                    content=f"un={user}&pw={hex_hlp85(password)}",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                await asyncio.sleep(15)
                auth = await client.post(
                    "/folio/authCustomer",
                    content=f"un={user}&pw={hex_hlp85(password)}",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                print("auth2", auth.text, flush=True)
            if not auth.text.startswith("OK||OK"):
                raise RuntimeError(auth.text)
        cookies = {c.name: c.value for c in client.cookies.jar}
        html = (await client.get("/customers/wa/")).text
        m = re.search(r"id:\s*(\d+)", html)
        uid = int(m.group(1)) if m else 0
        mm = re.search(r"tradeAccounts\s*:\s*\[([^\]]*)\]", html)
        accts = [int(x) for x in re.findall(r"\d+", mm.group(1))] if mm else []
        return cookies, uid, accts


async def ask(ws, payload, def_id, timeout=12.0):
    body = {**payload, "defId": def_id}
    fut = asyncio.get_event_loop().create_future()

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
    await ws.send(json.dumps(body))
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        task.cancel()
        return None
    finally:
        if not task.done():
            task.cancel()


async def main():
    user = os.environ["ATLAS_USER"]
    password = os.environ["ATLAS_PASS"]
    cookies, uid, accts = await login(user, password)
    acct = accts[0] if accts else None
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    results = []
    async with websockets.connect(
        f"{WS_URL}?id={uid}",
        additional_headers={"Cookie": cookie_header, "Origin": BASE, "User-Agent": "Mozilla/5.0"},
        max_size=20_000_000,
    ) as ws:
        did = 1

        async def run(payload, timeout=12):
            nonlocal did
            print(">>", payload, flush=True)
            msg = await ask(ws, payload, did, timeout=timeout)
            did += 1
            if msg is None:
                print("<< TIMEOUT", flush=True)
                results.append({"payload": payload, "timedOut": True})
                return None
            resp = msg.get("resp")
            info = {
                "payload": payload,
                "total": msg.get("total"),
                "summary": msg.get("summary"),
                "respType": "list" if isinstance(resp, list) else type(resp).__name__,
                "len": len(resp) if isinstance(resp, list) else None,
                "sample": resp[:5] if isinstance(resp, list) else resp,
            }
            print("<<", info["respType"], info.get("len"), str(info["sample"])[:250], flush=True)
            results.append(info)
            return msg

        # session list
        await run({"svc": "ftwscss", "inf": "ssn", "actn": "r"})
        await run({"svc": "ftwsfes", "inf": "snlst", "actn": "r"})
        await run({"svc": "ftwsfes", "actn": "r"})
        # exchange select often provides xId=1 NGX
        await run({"svc": "ftwsexchs", "inf": "slct", "actn": "r"})

        # try portfolio queries with sId / xId combinations
        for sId in ["1", 1, "0", 0]:
            for query in [
                {"sId": sId},
                {"sId": sId, "acct": acct},
                {"sId": sId, "acct": acct, "xId": "1"},
                {"sId": sId, "acct": str(acct), "xId": 1},
                {"sId": sId, "xId": "1"},
            ]:
                await run(
                    {"svc": "ftwspfs", "actn": "q", "inf": "lst", "query": query, "start": 0, "count": 100},
                    timeout=8,
                )

        # orders with session
        await run(
            {"svc": "ftwsoms", "actn": "q", "inf": "lst", "query": {"sId": "1", "acct": acct}, "start": 0, "count": 50},
            timeout=8,
        )
        await run(
            {
                "svc": "ftwsoms",
                "actn": "q",
                "inf": "lst",
                "query": {"sId": "1", "acct": acct, "xId": "1"},
                "start": 0,
                "count": 50,
            },
            timeout=8,
        )

    OUT.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT)


if __name__ == "__main__":
    asyncio.run(main())
