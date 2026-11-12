# InvestNaija connector

**Implementation status: `operational`** — login, TradeIN sync, wallet NUBAN deposits, NGX instruments, and order fee preview were live-verified on 2026-11-05. Equity order *placement* hits a broker-side deserialize error on `/investing/api/v1/orders` for the observed payload (see blockers).

## Surface

Angular app https://app.investnaija.com → `https://invest-chd.zanibal.com/api/v1`.

| Concern | Endpoint / notes |
|---------|------------------|
| Login | `POST /auth/customers/login` — password RSA-OAEP(SHA-256) with embedded SPKI public key; response top-level `token` + `uuid_token` |
| Auth headers | `Authorization: <JWT>` (**no** Bearer prefix) + `x-uuid-token` |
| Secure header | `x-secure-token: <portfolio.signature>` for portfolio detail + order calls |
| Accounts | `GET /trades/portfolios` (TradeIN) + synthetic `wallet:NGN` |
| Holdings / cash | TradeIN list (or detail) — `portfolioHoldings`, `availableCash` |
| Deposit instructions | `GET /transactions/wallet/balance` → `vNuban` / `virtualAccounts` (Wema) |
| Instruments | `GET /trades/securities` |
| Fee preview | `POST /trades/trade-order/calculate-price` |
| Place order | `POST /trades/trade-order/create` (+ secure token) |

## Live verification

```powershell
$env:IN_USER='you@email.com'; $env:IN_PASS='...'
$env:RECORD_LIVE_ORDER='1'   # optional; attempts buy+sell
.\.venv\Scripts\python.exe scripts\record_investnaija_api_flow.py
```

Transcript: `logs/investnaija_api_full_flow.json`.

## Caveats

- InvestIN asset portfolios may return “unavailable”; list/detail is skipped.
- SaveIN / PlanIN / mutual funds are not imported.
- Wallet NUBAN credits the **IN Wallet**; moving funds into TradeIN is done in the InvestNaija app (not automated).
- Order create: CHD validates numeric `limitPrice`/`marketPrice`, then proxies to Java `/investing/api/v1/orders`, which currently responds `Failed to read request` for that body. Calculate-price with the same fields succeeds.
