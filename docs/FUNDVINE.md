# Fundvine Connector Status

**Implementation status: `operational`** (verified with authorized live login, 2026-11-12)

## Platform

- Portal: `https://etrade.fundvinesecurities.com` (NaYa OMS / Global Trybe)
- Auth: ASP.NET `POST /Login.aspx` (`txtusername` / `txtpassword`) → cookie `__NAYA.AUTH` (+ `ASP.NET_SessionId`)
- JSON APIs: `POST /Services/NaYaOMSServices.asmx/<Op>` with `Content-Type: application/json` and ASMX `{d: ...}` envelopes
- Connector type: `authenticated_http`; cookies stored encrypted as `options_patch["api_session"]`
- Shared base: `app/connectors/naya_oms/` (same stack as ICON Stock Brokers)

## Verified flow

1. Form login → session cookies
2. `GetCSCSAccounts` — trading accounts (`CSCSNumber`, `ClientId`)
3. `GetStockPositions` — holdings + cash / portfolio summary
4. `GetAccountStatements` — ledger credits/debits
5. `GetAccountAttributes` — funding reference (CSCS) + Paystack portal URL (no dedicated NUBAN)
6. `GetSecurities` — NGX equities (~396 symbols live)
7. Equity buy/sell via `Client/EqtyMandate.aspx` WebForms postback after `IsMandateValid` / `GetPurchaseInfo`

## Observed holdings fields

| API field | Normalized field |
|-----------|------------------|
| `Security` | `symbol` |
| `Quantity` | `quantity` |
| `ClosingPrice` / `ESP` | `current_price` |
| `MarketValue` | `market_value` |
| `BEP` | `average_cost` |
| `ActualValue` | `cost_basis` |
| `Gain` / `GainHtml` | `unrealized_pnl` |

## Caveats

- No public official API docs; endpoints observed from an authorized session.
- Funding is **Paystack card** via `/Client/Deposit.aspx` — not a dedicated virtual account. Deposit instructions return CSCS as reference + portal URL.
- Equity order placement is an ASP.NET postback (not a clean JSON create). Empty cash → buy rejected with a clear validation error.
- Order-history `.ashx` handlers often return an HTML shell when called outside the SPA; statements/dashboard cover activity for sync.

## Manual re-test

```powershell
$env:FV_USER='your@email.com'
$env:FV_PASS='your-password'
python scripts\record_fundvine_api_flow.py
# optional live orders (needs cash/holdings):
$env:RECORD_LIVE_ORDER='1'
python scripts\record_fundvine_api_flow.py
```

Prefer storing credentials only via `POST /api/v1/connections` (encrypted at rest), not in shell history when possible.
