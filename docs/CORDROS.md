# Cordros Capital (CAPE) Connector Status

**Implementation status: `scaffolded`** — login, profile, deposit instructions and market data were
replayed live (2026-11-02) and the connector passed a live-data replay (10/10). Portfolio, cash,
trades and orders are built from the web app code but **cannot be exercised on the discovery account,
which has no Cordros customer record yet (KYC pending)**. Flip to `operational` after
`scripts/record_cordros_flow.py` passes on an account with a customer record.

## Platform

- Web app: `https://app.cordros.com` (jQuery + axios); API: `https://unified.cordros.com/api/v1/`
- Auth: `POST auth/login {email (lower-cased), password}` → `{access_token, user{customer{...}}}`; Bearer JWT (~6 h).
  `POST auth/refresh` (Bearer) → new token. `GET auth/user-profile` → `{success, data: user}`.
- The login page runs **reCAPTCHA v3**, but its token is **not sent** with the login request, so plain httpx
  login works without interacting with the captcha.
- Stock data and orders go through one gateway: `POST general-stock-api {endpoint: "<Op>/<args>", platform: "ST", payload?}`
  (Cordros "SymPlus" backend). Envelope `{success, message, data: {status, result: [{<Op>: [rows]}]}}`;
  `data: null` when the record doesn't exist.
- Connector type: `authenticated_http`; token stored encrypted as `options_patch["api_session"]`.

## Customer identifiers (from `user.customer`, only after KYC approval)

| Field | Used for |
|-------|----------|
| `stId` | `GetMarketAccounts/{stId}` → market accounts (`MARKET_ACCOUNT_ID`, `MARKET_ID`, `CASH_FUNDING_ACCOUNT_ID`) |
| `cscs` | `GetStockPortfolio/{cscs}`, `GetExecutedOrders/{cscs}`, `GetMarketOpenOrders/{cscs}` |
| `id` | `customer-transactions/{id}` (asset-management side) |
| `amId` | asset management (mutual funds) — not imported yet |

## Operations → capabilities

| Capability | Call |
|------------|------|
| Accounts | `GetMarketAccounts/{stId}` (+ cash per account). **No customer → `[]`** |
| Holdings | `GetStockPortfolio/{cscs}` rows (`SYMBOL_IDENTIFIER`, `SYMBOL_DESCRIPTION`, `UNIT_QUANTITY`, `AVERAGE_COST_PRICE`, `AVERAGE_COST_VALUE`, `MARKET_PRICE`, `MARKET_VALUE`); summary rows `= CASH -`, `= NSE1 TOTAL -`, `== GRAND TOTAL ==` excluded |
| Cash | `/GetCashBalance/{market_account_id}` → `AVAILABLE_BALANCE` |
| Summary | Portfolio totals rows + holdings cost |
| Transactions | `GetTradeByPostDate/{market_account_id}/{start}/{end}` (365 days) |
| Deposit instructions | `GET securities/portfolio-configuration` → `payment_info` HTML → **CORDROS SECURITIES LIMITED / 0000460692 / STANBIC IBTC BANK** (shared collection account; works pre-KYC; 3rd-party transfers not allowed) |
| Instruments | `GetMarketStockPrices` (188 NGX rows live) — symbol, description, board (`XNSA-EQTY/PREMIUM/ETF…`), current price |
| Orders | `/DoBuyOrder/` / `/DoSellOrder/` with `payload.{buyorder|sellorder}[{market_id, market_accountid, symbol_code, order_quantity, order_type M/L, limit_price, time_in_force DAY/GTD, expiry_date YYYY-MM-DD}]`; success = `success: true`. No review step in the web app |

## Caveats

- Pre-KYC: no accounts; holdings/cash/summary/transactions return 404 and orders 501 with a clear message.
- Order success payload (order id) not observed; result is `submitted` + broker message.
- The "I have paid" deposit notification (`payments/st-cash-deposit`) and withdrawals are not automated.
- Update/cancel order (`DoUpdateBuyOrder`, `DoCancelOrder`) observed but not exposed (no contract method).

## Live check

```powershell
$env:CD_USER='you@email.com'; $env:CD_PASS='...'
python scripts\record_cordros_flow.py
```
Read-only. Before KYC it will show 0 accounts but deposit instructions and instruments should pass.
