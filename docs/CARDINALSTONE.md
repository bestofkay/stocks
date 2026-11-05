# CardinalStone Connector Status

**Implementation status: `operational`** (live-verified 2026-11-04)

## Platform

- Web app: `https://app.cardinalstone.com` (React SPA, behind a Cloudflare challenge)
- API: `https://core2.cardinalstone.com` — JSON, `Authorization: Bearer <JWT>`; **not**
  behind the Cloudflare challenge, so the connector uses plain `httpx` (no browser)
- API paths are base64-obfuscated in the JS bundle (`atob("...")`); decoded and replayed
- Connector type: `authenticated_http`

## Auth flow

1. `POST /auth/login` `{username, password, deviceInfo}` → `{token, refresh_token, user{CustID, SEC_ID, is2FAEnabled}}`
   (`username` accepts CustID, username or email)
2. If `user.is2FAEnabled`: status `mfa_required` → submit the 6-digit authenticator code via
   `POST /api/v1/connections/{id}/mfa`; next sync calls `POST /auth/verify-2fa` `{CustID, token}`
3. Tokens are returned as `options_patch["api_session"]` and stored **encrypted** in the
   connection options. Later syncs reuse them (`GET /users/profile`), refreshing via
   `POST /auth/refresh` (Bearer refresh token) on 401, and fall back to password login.
   `has_broker_session` is true while a stored session exists.

## Endpoints → capabilities

| Capability | Endpoint(s) |
|------------|-------------|
| Accounts | `GET /zanibal/investment-accounts/{SEC_ID}` + synthetic `wallet:NGN` account |
| Holdings | `GET /zanibal/trading-account-report/{accountId}/{YYYY-MM-DD}` → `positionInstruments` |
| Cash | Trading: `GET /zanibal/investment-accounts/details/{id}`; Wallet: `GET /wallet/wallet-balance` |
| Portfolio summary | Trading account report `totalValue / totalCost / gainLoss` |
| Transactions | `GET /zanibal/accountStatement/{id}?startDate&endDate` (last 365 days) |
| Deposit instructions | `GET /wallet/nuban-accounts` — NUBAN (Nombank MFB via Nomba) → **NGN Wallet** |
| Markets / instruments | NGX; `GET /zanibal/equities?page&size=100` (paged locally) |
| Orders | `POST /zanibal/validate-order` then `POST /zanibal/orders`; amount orders use `/orders/calculate` |

### Order body

```json
{"accountId": "<investment account id>", "tradingAccountNo": "<CSCS no>",
 "side": "BUY|SELL", "secId": "FIDSON", "requestedQty": 10,
 "limitPrice": 80.0, "assetType": "EQUITY", "tif": "DAY|GOOD_TILL_DATE",
 "expires": "YYYY-MM-DD", "notes": "Placed via Portfolio Aggregator"}
```

`limitPrice` omitted ⇒ MARKET (DAY only). Whole shares only. No transaction PIN.

## Live verification

```powershell
$env:CS_USER='you@email.com'
$env:CS_PASS='...'
# optional live buy+sell (1 share default):
$env:RECORD_LIVE_ORDER='1'
$env:ORDER_SYMBOL='ACCESSCORP'
$env:ORDER_QTY='1'
python scripts\record_cardinalstone_flow.py

# Full /api/v1 flow (server must be running):
python scripts\record_cardinalstone_api_flow.py
```

Never commit credentials or `logs/cardinalstone_*.json`.

## Remaining caveats

- Deposits credit the NGN Wallet; moving wallet funds into the trading account is not automated.
- Mutual funds / fixed income (`/neulogic/*`) are not imported as holdings yet.
- Cancel order is observed but not exposed on the aggregator API.
