# ICON Stock Brokers Connector Status

**Implementation status: `operational`** (verified with authorized live login, 2026-11-12)

## Platform

- Portal: `https://etrade.iconstockbrokersltd.com` (ICON Trade — NaYa OMS / Global Trybe)
- Auth: ASP.NET `POST /Login.aspx` (`txtusername` / `txtpassword`) → cookie `__NAYA.AUTH` (+ `ASP.NET_SessionId`)
- JSON APIs: `POST /Services/NaYaOMSServices.asmx/<Op>` with `Content-Type: application/json` and ASMX `{d: ...}` envelopes
- Connector type: `authenticated_http`; cookies stored encrypted as `options_patch["api_session"]`
- Shared base: `app/connectors/naya_oms/` (same stack as Fundvine)

## Verified flow

1. Form login → session cookies
2. `GetCSCSAccounts` — trading accounts (`CSCSNumber`, `ClientId`)
3. `GetStockPositions` — holdings + cash / portfolio summary
4. `GetAccountStatements` — ledger credits/debits
5. `GetAccountAttributes` — funding reference (CSCS) + Paystack portal URL (no dedicated NUBAN)
6. `GetSecurities` — NGX equities
7. Equity buy/sell via `Client/EqtyMandate.aspx` WebForms postback after `IsMandateValid` / `GetPurchaseInfo`

## Caveats

- No public official API docs; endpoints observed from an authorized session.
- Funding is **Paystack card** via `/Client/Deposit.aspx` — not a dedicated virtual account.
- Equity order placement is an ASP.NET postback (not a clean JSON create).

## Manual re-test

```powershell
$env:ICON_USER='your@email.com'
$env:ICON_PASS='your-password'
python scripts\record_iconstock_flow.py
# optional live orders (needs cash/holdings):
$env:RECORD_LIVE_ORDER='1'
python scripts\record_iconstock_flow.py
```

Prefer storing credentials only via `POST /api/v1/connections` (encrypted at rest), not in shell history when possible.
