#!/usr/bin/env python3
"""Find Supabase project URL / anon key in Morgan Capital Next.js bundles."""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "morgan_supabase_probe.json"

UA = {"User-Agent": "Mozilla/5.0"}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", "replace")


def main() -> None:
    html = fetch("https://www.morgancapitalgroup.com/sign-in")
    chunks = sorted(set(re.findall(r"/_next/static/[^\"']+\.js", html)))
    # Also pull build manifest for more chunk names
    m = re.search(r"/_next/static/([^/]+)/_buildManifest\.js", html)
    if m:
        build = m.group(1)
        try:
            man = fetch(f"https://www.morgancapitalgroup.com/_next/static/{build}/_buildManifest.js")
            chunks += re.findall(r"/_next/static/[^\"']+\.js", man)
            chunks = sorted(set(chunks))
        except Exception as exc:
            print("manifest fail", exc)

    found_urls: list[str] = []
    found_keys: list[str] = []
    hits: list[str] = []

    for c in chunks:
        u = "https://www.morgancapitalgroup.com" + c
        try:
            body = fetch(u)
        except Exception:
            continue
        if "supabase" not in body.lower() and "eyJ" not in body:
            continue
        hits.append(c)
        for murl in re.findall(r"https://[a-z0-9-]+\.supabase\.co(?:/[a-zA-Z0-9_/-]*)?", body):
            base = re.match(r"https://[a-z0-9-]+\.supabase\.co", murl)
            if base and base.group(0) not in found_urls:
                found_urls.append(base.group(0))
        for key in re.findall(r"eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+", body):
            if key not in found_keys:
                found_keys.append(key)
        # env-style
        for m2 in re.findall(
            r"(?:SUPABASE_URL|NEXT_PUBLIC_SUPABASE_URL)[\"'\\s:=]+(https://[a-z0-9-]+\.supabase\.co)",
            body,
        ):
            if m2 not in found_urls:
                found_urls.append(m2)

    payload = {"urls": found_urls, "keys": found_keys, "hits": hits, "chunk_count": len(chunks)}
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"urls": found_urls, "key_count": len(found_keys), "hits": len(hits)}, indent=2))


if __name__ == "__main__":
    main()
