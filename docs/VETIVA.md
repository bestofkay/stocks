# Vetiva Connector Status

**Implementation status: `scaffolded`** — endpoints observed live in an authorized session
(2026-10-31); connector passes mocked flow tests. Flip to `operational` after
`scripts/record_vetiva_flow.py` passes from your machine.

## Platform

- Portal: `https://client.vetiva.com` (Next.js). Login page `/v2`.
- API: same-origin proxy `https://client.vetiva.com/api/proxy/*`, JSON,
  `Authorization: Bearer <JWT>` (header alone is enough; no cookies). No Cloudflare challenge.
- Envelope: `{message, status_code, data}`; errors `{error, reason, status_code}`.
- Connector type: `authenticated_http` (httpx, no browser).

## Auth flow

1. `POST /auth/login` `{email, password, channel:"web"}` → `data.session{access_token, refresh_token, expired_at, refresh_expires_at, account_id}`
2. If `data.is2faenabled`: Vetiva **emails** a 6-digit code → status `mfa_required`. Submit it via
   `POST /api/v1/connections/{id}/mfa`; the next sync calls `POST /auth/verify-2fa`
   `{channel, email, password, token}` directly (no second login, so no second email).
3. Tokens stored encrypted in `options_patch["api_session"]`; reuse checks `GET /account/individual?channel=web`,
   refreshes via `POST /auth/refresh {refresh_token}` on 401/403. Observed lifetimes: access ≈15 min,
   refresh ≈4 h, so daily syncs usually re-login.
4. **One active session per user**: a connector login signs the web portal out (and a web login
   invalidates the connector's tokens). The connector does not call `/auth/logout`.

## Endpoints → capabilities

| Capability | Endpoint(s) |
|------------|-------------|
| Accounts | `portfolio:<account_id>` (trading cash from `GET /portfolio/value` `trading_balance`) + one account per wallet from `GET /payment/get-account-wallets` |
| Holdings | `GET /portfolio/assets?page&page_size` — stocks, fixed income, money market, trusts |
| Cash | Portfolio: `trading_balance`; wallet: `available_balance` |
| Summary | `GET /portfolio/value` + `GET /portfolio/breakdown-expanded` (`unrealized_pnl`) |
| Transactions | Wallet: `GET /payment/get-wallet-transactions`; trading: `GET /vsl/trading-account/transactions` (only once a trade account exists) — last 365 days |
| Instruments | `GET /vsl/list-securities?page&size` (130 NGX equities observed), price stats `GET /vsl/get-sec-stats?symbol=` |
| Orders | `POST /vsl/order/fee-preview` then `POST /vsl/order` with `Idempotency-Key` header |
| Deposit instructions | **Not offered** — Vetiva has no dedicated virtual account; funding is a hosted checkout (`POST /payment/initiate-payment` → `checkout_url`) |

### Holdings field mapping

| API field | Normalized |
|-----------|------------|
| `asset_symbol` | `symbol` (blank for funds → stable id `vetiva:<id>`) |
| `asset_name` | `security_name` |
| `asset_type` | `asset_type` (`equity`, `fixed_income`, `money_market`, `trust`) |
| `quantity_or_units` | `quantity` |
| `amount` | `market_value` |
| `unrealized_pnl` | `unrealized_pnl` |
| `amount / quantity` | `current_price` (derived) |
| `amount - unrealized_pnl` | `cost_basis` (derived; `None` if P&L unknown) |

### Order body (all codes are strings, as the portal sends them)

```json
{"type": "1|2", "symbol": "DANGCEM", "qty": 10, "side": "1|2", "tif": "0|6", "gtd": "YYYY-MM-DD", "px": "1400.50"}
```

`type` 1=market, 2=limit · `side` 1=buy, 2=sell · `tif` 0=Day, 6=GTD (`gtd` required) · `px` only for limit.
Amount orders convert NGN → whole shares using the limit price or `lastPx`. Orders placed
while the market is closed come back `queued` (with `queue_id`, `next_open_at`). No PIN is sent.

## Remaining caveats

- Discovery account: KYC not started, **no VSL trade account** → fee preview/orders return
  *You Do Not Have A Trade Account Yet*; the connector returns 501 until `has_trade_account` is true.
- Holdings and order-success payloads parsed from portal code (account was empty).
- Wallet transaction row fields are parsed defensively (none observed).
- Cancel (`POST /vsl/order/cancel`) observed but not exposed (no cancel in the connector contract).

## Promote to operational

```powershell
$env:VT_USER='you@email.com'
$env:VT_PASS='...'
python scripts\record_vetiva_flow.py   # signs the web portal out (single session)
```

Read-only. On `ALL PASSED`, set `ImplementationStatus.OPERATIONAL` / `operational=True` in
`app/connectors/vetiva/connector.py` and update `tests/test_registry.py` and the provider tables.
