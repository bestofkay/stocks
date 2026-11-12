#!/usr/bin/env python3
import re
from pathlib import Path
import httpx

OUT = Path(__file__).resolve().parents[1] / "logs" / "investnaija_chunks.txt"
text = httpx.get("https://app.investnaija.com/main.11b3686bf10f5c65.js", timeout=120).text
jsrefs = sorted(set(re.findall(r"([a-zA-Z0-9_\-]+\.[a-f0-9]{12,}\.js)", text)))
counts = {pat: text.lower().count(pat.lower()) for pat in [
    "trades", "tradeOrder", "TradeOrder", "calculatePrice", "wallet", "nuban",
    "secure-token", "secureToken", "quantityRequested", "orderType", "limitPrice",
]}
# Angular lazy: loadChildren or import(
imports = sorted(set(re.findall(r"import\([\"']([^\"']+)[\"']\)", text)))
# webpack chunk map often like {123:"hash"
chunk_map = re.findall(r"(\d{1,4}):\"([a-f0-9]{8,})\"", text)
print("jsrefs", len(jsrefs), jsrefs[:20])
print("imports", imports[:30])
print("chunk_map sample", chunk_map[:20], "count", len(chunk_map))
print("counts", counts)
OUT.write_text(
    "\n".join([
        f"jsrefs={jsrefs}",
        f"imports={imports}",
        f"chunk_map_count={len(chunk_map)}",
        f"counts={counts}",
    ]),
    encoding="utf-8",
)
