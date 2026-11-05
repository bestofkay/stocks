# InvestNow (United Capital) Connector Status

**Implementation status: `scaffolded`** — browser-driven connector built from a live session
(2026-11-03). Parsers verified against the live response shapes; the discovery account has no
products yet, so holdings are covered by synthetic fixtures built from the app's own field names.

## Why browser-driven

app.investnow.ng (Next.js) encrypts login fields (username, password, email, BVN, NIN, PINs, …) in
the browser before sending them to `api.investnow.ng`. Rather than re-implementing that, the connector
**drives the real login form in Playwright** (email → Continue → password) so the site does its own
encryption, then **captures the JSON the app loads** while opening `/portfolio` and `/dashboard`.
It never replays encrypted endpoints. Same pattern as the Afrinvestor connector.

- Imperva (Incapsula) fronts the site. A challenge/block page is reported as `captcha_required`
  and is never bypassed; retry with `headless=false` (connection option) and complete any check yourself.
- A one-time-code prompt → `waiting_for_user` (complete it on the website).
- Navigation is restricted to `app.investnow.ng` / `api.investnow.ng`.

## Data captured

| API (loaded by the app) | Used for |
|-------------------------|----------|
| `GET /api/v2/portfolios/get-portfolio-items` | Accounts, holdings, cash, summary. Groups `assetManagement` (mutual funds), `trustees`, `wealthManagement` → `[{customerInformation, portfolios:[{cashAccount, fundAccount, placement}]}]` |
| `GET /api/v1/accounts/get-invoice-history` | Deposit transactions |
| `GET /api/v1/portfolios/get-withdrawal-requests` | Withdrawal transactions (when the app loads it) |
| `GET /api/v1/customers/get` | Profile (not stored) |

Field mapping per portfolio: account = `cashAccount.account_NO` (cash `current_BALANCE`, currency
`currency_ID`); holding value = `fundAccount.currenT_VALUE` or `placement.current_BALANCE_AMOUNT`;
cost = `fundAccount.totaL_PURCHASE_AMOUNT` or `placement.investment_AMOUNT`; name = `placement.product_DESC`
or `cashAccount.account_NAME`.

## Not supported (by design of the platform)

- **Stocks**: holdings/balances stream over a WebSocket from a separate trading backend, and buying/selling
  happens on **trade.investnow.ng** (a different platform the app opens in a new tab). No orders/instruments here.
- **Deposit instructions**: funding is per-payment invoices; no static virtual account observed.

## Live check (your machine; needs Playwright + Chromium)

```powershell
$env:IN_USER='you@email.com'; $env:IN_PASS='...'
$env:IN_HEADED='1'   # optional: show the browser (helps if Imperva challenges headless)
python scripts\record_investnow_flow.py
```
