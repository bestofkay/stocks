#!/usr/bin/env python3
"""Retry Atlas login until concurrent lock clears; try kick variants."""

from __future__ import annotations

import hashlib
import time
import httpx

BASE = "https://app.atlassportfolios.com"
USER = "SOO25720"
PASS = "5T3ll@2025"


def h85(s: str) -> str:
    a = hashlib.sha512(s.encode()).hexdigest()
    return hashlib.sha512(a.encode()).hexdigest()


def main() -> None:
    with httpx.Client(
        base_url=BASE,
        timeout=60,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0", "Origin": BASE, "Referer": f"{BASE}/customers/"},
    ) as c:
        for i in range(40):
            c.cookies.clear()
            c.get("/customers/")
            # kick variants
            for path, body in [
                ("/folio/authCustomer?a=o", f"un={USER}"),
                ("/folio/authCustomer?a=o", f"un={USER}&pw={h85(PASS)}"),
                ("/folio/authCustomer?a=k", f"un={USER}&pw={h85(PASS)}"),
                ("/folio/resetlogin?t=c", f"un={USER}"),
                ("/folio/logout", f"un={USER}"),
            ]:
                r = c.post(path, content=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
                if r.text:
                    print(f"kick {path} -> {r.text[:80]!r}")
            v = c.post(
                "/folio/authCustomer?a=v",
                content=f"un={USER}",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            auth = c.post(
                "/folio/authCustomer",
                content=f"un={USER}&pw={h85(PASS)}",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            print(f"[{i}] v={v.text!r} auth={auth.text!r} cookies={list(c.cookies.keys())}", flush=True)
            if auth.text.startswith("OK||OK"):
                wa = c.get("/customers/wa/")
                print("LOGIN OK", "loginContent" in wa.text, len(wa.text))
                print("cookies", dict(c.cookies))
                return
            time.sleep(30)
        raise SystemExit("gave up")


if __name__ == "__main__":
    main()
