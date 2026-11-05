# Afrinvestor Connector Status

**Implementation status: `operational`** (verified with authorized live login, 2026-10-27)

## Verified flow

1. Browser login at `https://afrinvestor.com/auth` (email + password, Playwright)
2. Session cookie reused for same-origin authenticated APIs:
   - `GET /api/all/portfolio/list` — accounts
   - `GET /api/all/portfolio/assets/{portfolio_id}/equity/holdings` — holdings
   - `GET /api/all/wallet` — cash
   - `GET /api/all/portfolio/nets` — summary valuations
   - `POST /api/all/wallet/dedicated` — dedicated virtual-account funding details
   - `GET /api/all/invest/equities?marketId=NGX&...` — tradeable NGX equities (~190 symbols)
   - `POST /api/all/invest/equities/buy/review` then `.../buy` — equity buy
   - `POST /api/all/invest/equities/sell/review` then `.../sell` — equity sell
   - `GET /api/all/invest/equities/orders?portfolioId=` — order history (broker-native)
3. Browser context closed after sync / deposit / order calls

## Observed holdings fields

| API field | Normalized field |
|-----------|------------------|
| `security_name` | `symbol` |
| `security_label` | `security_name` |
| `quantity_held` | `quantity` |
| `market_price` | `current_price` |
| `market_value` | `market_value` |
| `cost_basis` | `average_cost` (per unit) |
| `average_cost * quantity` | `cost_basis` (total) |
| `total_gain` | `unrealized_pnl` |
| `value_date` | `valuation_timestamp` |

## Remaining caveats

- No public official API docs; endpoints were observed from an authorized browser session.
- PIN/OTP challenges (if presented) are supported via MFA workflow but not always required.
- Money-market / fixed-deposit positions are not yet imported as holdings (summary nets expose valuations only).
- Deposit support returns dedicated VA details only (`GET /api/v1/deposits/instructions`) — it does not initiate card/transfer funding.
- Equity buy/sell via `POST /api/v1/orders` places live broker orders; pass optional `pin` when the account requires it. Withdrawals/transfers are not supported.

## Manual re-test

```powershell
$env:AFR_USER='your@email.com'
$env:AFR_PASS='your-password'
# run connector authenticate + get_accounts + get_holdings via a local script
```

Prefer storing credentials only via `POST /api/v1/connections` (encrypted at rest), not in shell history when possible.
