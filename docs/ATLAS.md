# Atlass Portfolios (CitiTrader) Connector Status

**Implementation status: `operational`** (verified with authorized live login, 2026-11-12)

## Platform

- Portal: `https://app.atlassportfolios.com/customers/` (CitiTrader — Caladata Folio)
- Auth: `POST /folio/authCustomer?a=v` (password version) then `POST /folio/authCustomer` with `un` + double-SHA-512 hex password
- Session cookies: `JSESSIONID`, `U_ID`
- Data plane: WebSocket `wss://app.atlassportfolios.com/cftrader?id={userId}` (Folio JSON `svc` / `inf` / `actn`)
- Logout (required): `GET /customers/logoutUser?e=ft` — portal enforces **single concurrent login**
- Connector type: `authenticated_http` (+ WebSocket); cookies/settings stored encrypted as `options_patch["api_session"]`

## Verified flow

1. Form login → session cookies + WA settings bootstrap (`tradeAccounts`, `id`, `nonce`)
2. `ftwsdshbrds` / `all|acct|pf` — cash, order counts, portfolio valuation summary
3. `ftwsexchs` / `slct` — NGX / NASD markets
4. `ftwsmds` / `mde` — securities universe (best during market hours)
5. `ftwspfs` / `lst` — per-symbol portfolio grid (session/account query; may be empty outside a list session)
6. Deposit instructions — WebPay disabled on this portal (`canPayOnline=false`); returns trading-account reference + broker contact note

## Caveats

- Provider allows only one active login; always call logout / `disconnect()` to release the lock.
- Per-symbol holdings depend on Folio portfolio grid query; when empty, connector falls back to asset-class summary from the dashboard.
- Online card funding is off for this white-label; fund via Atlass using the trading account reference.
- Equity orders use `ftwsoms` when `isTrader`, otherwise `ftwsmndts` mandates (`symbl` / `lmtPx`; account as string). Requires an open FIX session from `ftwsfes`/`snlst` with `on:true`. Duplicate/KYC warnings are re-sent with `ovrdOrd` / `ovrdKyc`.

## Manual re-test

```powershell
$env:ATLAS_USER='SOO25720'
$env:ATLAS_PASS='your-password'
python scripts\record_atlas_flow.py
```
