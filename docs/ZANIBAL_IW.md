# Zanibal E-Business Suite connectors (WSTC, SISL)

**Status: `scaffolded`** for both. WSTC pages/forms were observed in an authorized session (2026-11-02);
SISL shares the software and its login page was inspected (2026-10-31), but its post-login pages
were not observed. Flip each to `operational` after `scripts/record_iw_flow.py <provider>` passes.

| Provider id | Portal | Base path | Login password |
|-------------|--------|-----------|----------------|
| `wstc` | https://customerportal.wstc.com.ng | `/iwnewebiz` | AES-encrypted client-side (CryptoJS AES-128-CBC, key = IV = fixed string in the login page script) |
| `sisl` | https://www.sislbrokerage.com | `/IW` | Sent as typed; anti-forgery field is renamed `__RequestVerificationToken_<suffix>` |

## Design

- `app/connectors/zanibal_iw/` — shared base (`base.py`), stdlib HTML extractor (`html.py`), page parsers (`parser.py`).
  Not a connector itself (no `connector.py`), so discovery skips it.
- `app/connectors/wstc/connector.py`, `app/connectors/sisl/connector.py` — ~15-line subclasses (origin, base path, AES key or none).
- Adding another IW broker = one more subclass with its origin/base path; check the login page for client-side encryption.
- Plain httpx with a cookie jar; redirects followed manually and every hop checked against the allow-list.
- Columns are located by header text, not table id (ids differ between deployments).
- The cookie session is stored encrypted as `options_patch["api_session"]["cookies"]` and resumed when still valid.

## Pages → capabilities

| Capability | Page |
|------------|------|
| Accounts | `CustInfo/AccountStatement` → `MasterAccount` select (one stockbroking account) |
| Holdings — equities | `CustInfo/ChartSummaryPerStock`: Symbol, Name, Units, Avg Unit Price, Mkt. Value, Price, Cummulative Cost, Gain |
| Holdings — T-bills / bonds | `CustInfo/PortfolioHoldings` (Trans ID, Security Name, Discounted Value → market value, Face Value → quantity) |
| Cash | Statement "Total Balance" (fallback: Trade page "Deposit for shares account balance") |
| Summary | Summary Per Stock totals (Total Cost, Total Market Value, Total Gain/Loss) |
| Transactions | Statement POST (last 365 days; Debit/Credit) + `CustInfo/PortfolioTransactions` (type, symbol, units, price, fees, proceeds) |
| Instruments | `Research/MarketStats` (366 symbols with OHLC/close; no names) |
| Orders | `Trade/Trade` form → **Preview Order** POST → confirm form on the preview page (posted as-is) |
| Deposit instructions | **Not offered** (no funding account shown in the portal) |

### Order form fields (POST `Trade/Trade`)

`Action` Buy/Sell · `CSCSAcctNumber` · `Symbol` · `OrderType` Market/Limit · `PriceLimit` (0.0 for market) ·
`Units` · `DateLimit` (required; portal default ≈ +14 days kept unless `expires` given) · `Instructions` · anti-forgery token.
Amount orders use `Trade/LastTrade` (double-encoded JSON) for the price and round down to whole shares.

## Caveats

- **Trading locked on the discovery account:** the WSTC trade form's CSCS dropdown is empty, so the connector returns 501
  until a CSCS account is linked. The preview → confirm step has therefore not been observed; the connector refuses to
  place anything unless the preview page contains a recognisable confirm form, and surfaces portal error alerts as 422.
- A Terms and Conditions prompt (`Home/SubmitTerms`) is never auto-accepted; sync returns `waiting_for_user`.
- OTP: no OTP was seen at login; if one appears, the generic OTP form handler is used (`mfa_required` → `/mfa`).
- SISL post-login pages are assumed identical to WSTC's.

## Promote to operational

```powershell
$env:IW_USER='<user id>'; $env:IW_PASS='<password>'
python scripts\record_iw_flow.py wstc
python scripts\record_iw_flow.py sisl
```
Read-only. On `ALL PASSED`, set `implementation_status = ImplementationStatus.OPERATIONAL` on that subclass
and update `tests/test_registry.py` and the provider tables.
