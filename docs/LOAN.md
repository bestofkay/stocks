# Loan Collateral & Eligibility

Collateral for loan eligibility is derived from **persisted purchased holdings**
(market value only). Cash / deposit / wallet balances are **not** included.
Loan endpoints never call brokers live.

**LTV:** `eligible_loan = loan_ltv_ratio × holdings_market_value` (default **30%**, `LOAN_LTV_RATIO`).

## How assets stay current

| Event | Behaviour |
|-------|-----------|
| Connect provider | Auto-enqueues sync → upserts holdings |
| Login | Idle syncs enqueued for all connections (background) |
| Manual `POST /connections/{id}/sync` | Same upsert path |

## Endpoints

### `GET /api/v1/loan/collateral`

All selectable holdings across connections, per-provider and per-currency totals,
plus grand total when a single currency is present.

Asset ids: `holding:{id}` only.

### `GET /api/v1/loan/eligibility`

`0.3 ×` grand total of **all** saved holding market values.

### `POST /api/v1/loan/eligibility/selected`

Body:

```json
{
  "holding_ids": [12, 15],
  "asset_ids": ["holding:12"]
}
```

Returns `0.3 ×` sum of selected holding values. `cash:*` ids are rejected.

## Notes

- Totals are **not FX-converted**; multi-currency portfolios expose `by_currency`.
- Holdings marked `confirmed_absent` are excluded.
- Cash remains available on portfolio endpoints; it is simply out of scope for loans.
