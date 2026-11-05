# Portfolio Aggregator API Guide (For Beginners)

This guide explains **every endpoint** and walks you from zero to seeing your Afrinvestor holdings as JSON.

**Interactive docs** (server must be running):

| Tool | URL |
|------|-----|
| Swagger (try endpoints in browser) | http://127.0.0.1:8000/docs |
| ReDoc (readable reference) | http://127.0.0.1:8000/redoc |

**Base URL used in this guide:**

```text
http://127.0.0.1:8000
```

---

## 1. Big picture — what is what?

```text
┌─────────────────────┐
│  You / Postman /    │
│  another app        │
└──────────┬──────────┘
           │  REST calls (this guide)
           ▼
┌─────────────────────┐
│  Portfolio App API  │  ← auth, connections, sync, portfolio
│  (FastAPI)          │
└──────────┬──────────┘
           │  uses a "connector"
           ▼
┌─────────────────────┐
│  Afrinvestor        │  ← Playwright login + broker APIs
│  Connector          │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  afrinvestor.com    │
└─────────────────────┘
```

### Words that confuse beginners

| Word | Meaning |
|------|---------|
| **App user** | Your login for *this* portfolio service (`/auth/login`) |
| **Provider** | A broker type the system knows (`afrinvestor`, `morgan`, …) |
| **Connection** | *Your* saved link to a provider (encrypted username/password) |
| **Connector** | Python code that knows how to log in and fetch that broker |
| **Sync** | Job that runs the connector and saves holdings into MySQL |
| **Portfolio APIs** | Read the *saved* data (fast JSON; no live broker call unless you sync again) |

**Important:** Portfolio endpoints read from the database. If you never sync, holdings will be empty.

---

## 2. Start the server (once)

```powershell
cd c:\Projects\stocks
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

You also need MySQL configured, `.env` filled, and `alembic upgrade head` (see README).

### Health check (no login)

```http
GET /health
```

```json
{
  "status": "ok",
  "version": "0.1.0",
  "env": "development"
}
```

---

## 3. Authentication — who you are in *this* app

Most endpoints need:

```http
Authorization: Bearer <access_token>
```

### 3.1 Register (create account)

```http
POST /api/v1/auth/register
Content-Type: application/json
```

```json
{
  "email": "you@example.com",
  "password": "at-least-12-chars"
}
```

**Response `201`**

```json
{
  "id": 1,
  "email": "you@example.com",
  "is_active": true,
  "is_admin": true,
  "created_at": "2026-10-27T12:00:00Z"
}
```

Notes:

- Password minimum length: **12**
- First user becomes admin

---

### 3.2 Login (get tokens)

```http
POST /api/v1/auth/login
Content-Type: application/json
```

```json
{
  "email": "you@example.com",
  "password": "at-least-12-chars"
}
```

**Response `200`**

```json
{
  "access_token": "eyJ....",
  "refresh_token": "eyJ....",
  "token_type": "bearer",
  "expires_in": 86400
}
```

| Field | Use |
|-------|-----|
| `access_token` | Put in `Authorization` header for API calls |
| `refresh_token` | Get a new access token when it expires |
| `expires_in` | Seconds until access token expires (default 86400 = 1 day) |

**How to use the token**

```http
GET /api/v1/auth/me
Authorization: Bearer eyJ....
```

In Swagger: click **Authorize** → paste the access token.

---

### 3.3 Refresh token

When access token expires:

```http
POST /api/v1/auth/refresh
Content-Type: application/json
```

```json
{
  "refresh_token": "eyJ...."
}
```

**Response `200`** — same shape as login (`access_token`, `refresh_token`, `token_type`, `expires_in`).

Old refresh token is rotated (do not reuse the previous one).

---

### 3.4 Who am I?

```http
GET /api/v1/auth/me
Authorization: Bearer <access_token>
```

**Response `200`**

```json
{
  "id": 1,
  "email": "you@example.com",
  "is_active": true,
  "is_admin": true,
  "created_at": "2026-10-27T12:00:00Z"
}
```

---

### 3.5 Logout

```http
POST /api/v1/auth/logout
Authorization: Bearer <access_token>
```

**Response `204`** (empty body). Session is revoked.

---

### Auth error example

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Invalid email or password"
  }
}
```

---

## 4. Providers — which brokers exist?

Providers are **types of brokers** the app can talk to. No credentials here — just metadata.

These endpoints do **not** require a token.

### 4.1 List providers

```http
GET /api/v1/providers
```

**Response `200`**

```json
{
  "count": 2,
  "providers": [
    {
      "provider_id": "afrinvestor",
      "provider_name": "Afrinvestor",
      "connector_type": "hybrid",
      "implementation_status": "operational",
      "operational": true,
      "mfa_supported": true,
      "auth_methods": ["username_password"],
      "required_credential_fields": [
        {"name": "username", "label": "Email", "field_type": "email", "required": true, "secret": false},
        {"name": "password", "label": "Password", "field_type": "password", "required": true, "secret": true}
      ],
      "optional_credential_fields": [
        {"name": "otp", "label": "OTP / MFA code", "field_type": "otp", "required": false, "secret": true}
      ],
      "capabilities": ["accounts", "holdings", "cash_balance", "mfa"],
      "approved_domains": ["afrinvestor.com", "www.afrinvestor.com"],
      "notes": "..."
    }
  ]
}
```

Read this before creating a connection:

- `required_credential_fields` → what JSON keys to send in credentials
- `operational: true` → live-tested; `false` → not ready
- `connector_type` → how it works (`hybrid`, `browser_automation`, `official_api`, …)

### 4.2 One provider

```http
GET /api/v1/providers/afrinvestor
```

Same object as one item above.

---

## 5. Connections — save your broker login (encrypted)

A **connection** = “my Afrinvestor account, stored safely.”

All connection endpoints need `Authorization: Bearer <access_token>`.

### 5.1 Create connection

```http
POST /api/v1/connections
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "provider_id": "afrinvestor",
  "display_name": "My Afrinvestor",
  "credentials": {
    "username": "you@email.com",
    "password": "your-afrinvestor-password"
  },
  "options": {}
}
```

**Response `201`** (secrets never returned)

```json
{
  "id": 12,
  "provider_id": "afrinvestor",
  "provider_key": "afrinvestor",
  "display_name": "My Afrinvestor",
  "status": "configured",
  "last_sync_at": null,
  "last_successful_sync_at": null,
  "last_error_code": null,
  "has_credentials": true,
  "created_at": "2026-10-27T12:10:00Z",
  "updated_at": "2026-10-27T12:10:00Z"
}
```

Save `id` (example `12`) — you need it for sync.

Password is encrypted with AES-256-GCM. It is **never** returned in API responses.

### 5.2 List connections

```http
GET /api/v1/connections
Authorization: Bearer <access_token>
```

### 5.3 Get one connection

```http
GET /api/v1/connections/12
Authorization: Bearer <access_token>
```

### 5.4 Update connection

```http
PATCH /api/v1/connections/12
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "display_name": "Afrinvestor Main",
  "credentials": {
    "username": "you@email.com",
    "password": "new-password-if-changed"
  }
}
```

Send only fields you want to change. To replace password, send full `credentials` object again.

### 5.5 Delete connection

```http
DELETE /api/v1/connections/12
Authorization: Bearer <access_token>
```

**Response `204`**

### 5.6 Test connection (lightweight)

```http
POST /api/v1/connections/12/test
Authorization: Bearer <access_token>
```

For Afrinvestor this checks the login page is reachable / form found (not a full holdings sync).

**Response**

```json
{
  "success": true,
  "status": "authenticated",
  "message": "Login page reachable and form detected. Full auth not tested (scaffolded connector)."
}
```

(Message text may vary.)

### 5.7 Submit MFA / OTP (only if needed)

If sync stops with `mfa_required`:

```http
POST /api/v1/connections/12/mfa
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "code": "123456"
}
```

Then run sync again.

---

## 6. Sync — tell the connector to fetch live data

Sync = “go to Afrinvestor now, pull accounts/holdings/cash, save into MySQL.”

### 6.1 Start sync

```http
POST /api/v1/connections/12/sync
Authorization: Bearer <access_token>
```

**Response `202`** (accepted, runs in background)

```json
{
  "sync_id": 55,
  "status": "queued",
  "message": "Synchronization accepted",
  "poll_url": "/api/v1/syncs/55"
}
```

### 6.2 Poll sync status

```http
GET /api/v1/syncs/55
Authorization: Bearer <access_token>
```

**Response**

```json
{
  "id": 55,
  "connection_id": 12,
  "status": "completed",
  "current_step": "done",
  "progress": "100.00",
  "started_at": "2026-10-27T12:15:00Z",
  "completed_at": "2026-10-27T12:15:25Z",
  "records_found": 2,
  "accounts_found": 1,
  "holdings_found": 1,
  "error_code": null,
  "safe_error_message": null,
  "created_at": "2026-10-27T12:15:00Z"
}
```

### Sync status meanings

| Status | Meaning |
|--------|---------|
| `queued` | Waiting to start |
| `running` | Connector working |
| `waiting_for_user` | MFA / CAPTCHA / manual step |
| `completed` | Success |
| `completed_with_warnings` | Partial success |
| `failed` | Failed (old data kept) |
| `cancelled` | You cancelled it |

### 6.3 List all syncs

```http
GET /api/v1/syncs
Authorization: Bearer <access_token>
```

Optional: `GET /api/v1/syncs?connection_id=12`

### 6.4 Syncs for one connection

```http
GET /api/v1/connections/12/syncs
Authorization: Bearer <access_token>
```

### 6.5 Cancel a sync

```http
POST /api/v1/syncs/55/cancel
Authorization: Bearer <access_token>
```

Only works while status is `queued`, `running`, or `waiting_for_user`.

### What the Afrinvestor connector does during sync

1. Opens a headless browser → `https://afrinvestor.com/auth`
2. Fills email + password, signs in
3. Uses your session cookie to call Afrinvestor APIs:
   - `/api/all/portfolio/list` → accounts
   - `/api/all/portfolio/assets/{id}/equity/holdings` → holdings
   - `/api/all/wallet` → cash
   - `/api/all/portfolio/nets` → totals
4. Saves normalized data into MySQL
5. Closes the browser

You do not call those Afrinvestor URLs from Postman. The connector does.

---

## 7. Portfolio — read saved holdings / balance / accounts

All need `Authorization: Bearer <access_token>`.

These return data **already synced**. Run a sync first.

### 7.1 Summary (totals + cash by currency)

```http
GET /api/v1/portfolio/summary
Authorization: Bearer <access_token>
```

**What you get:** market value, cost basis, cash, P/L — **per currency** (NGN is not mixed with USD).

```json
{
  "by_currency": [
    {
      "currency": "NGN",
      "total_market_value": "2775.00",
      "total_cost_basis": "2817.00",
      "cash_value": "3134.63",
      "unrealized_pnl": "-41.99",
      "holdings_count": 1
    }
  ],
  "accounts_count": 1,
  "holdings_count": 1,
  "connections_count": 1,
  "as_of": "2026-10-22T00:00:00Z",
  "note": "Totals are kept per currency; amounts are not FX-converted."
}
```

→ This is your **balance / overview**.

---

### 7.2 Holdings (stocks you own)

```http
GET /api/v1/portfolio/holdings
Authorization: Bearer <access_token>
```

Optional filters:

```text
?provider=afrinvestor
&account_id=1
&connection_id=12
&symbol=ACCESSCORP
&asset_type=equity
&currency=NGN
```

```json
[
  {
    "id": 1,
    "account_id": 1,
    "connection_id": 12,
    "provider_id": "afrinvestor",
    "account_name": "Soji Okunuga",
    "security_identifier": "symbol:ACCESSCORP:NGN",
    "symbol": "ACCESSCORP",
    "security_name": "ACCESS HOLDINGS PLC",
    "isin": null,
    "asset_type": "equity",
    "quantity": "100",
    "average_cost": "28.17",
    "cost_basis": "2817.00",
    "current_price": "27.75",
    "market_value": "2775",
    "unrealized_pnl": "-41.99",
    "currency": "NGN",
    "valuation_timestamp": "2026-10-22T00:00:00Z",
    "observation_status": "observed"
  }
]
```

→ This is your **portfolio positions**.

---

### 7.3 Accounts

```http
GET /api/v1/portfolio/accounts
Authorization: Bearer <access_token>
```

Optional: `?provider=afrinvestor` `&connection_id=12`

```json
[
  {
    "id": 1,
    "connection_id": 12,
    "provider_id": "afrinvestor",
    "external_account_id": "00000000-0000-0000-0000-000000000000",
    "account_name": "Soji Okunuga",
    "account_type": "SELF",
    "currency": "NGN",
    "cash_balance": "3134.63",
    "last_seen_at": "2026-10-27T12:15:20Z"
  }
]
```

→ Broker **account** + wallet cash on that account.

---

### 7.4 By broker

```http
GET /api/v1/portfolio/by-broker
Authorization: Bearer <access_token>
```

One block per connection: totals + that broker’s holdings.

Useful when you add Broker B later and want them separate.

---

### 7.5 By security (same stock across brokers)

```http
GET /api/v1/portfolio/by-security
Authorization: Bearer <access_token>
```

Example idea: Afrinvestor 100 ACCESSCORP + another broker 50 → total 150, with both positions listed.

Merging prefers **ISIN**; symbol alone is not enough across different currencies.

---

### 7.6 History (snapshots after successful syncs)

```http
GET /api/v1/portfolio/history
Authorization: Bearer <access_token>
```

Optional filters: `date_from`, `date_to`, `provider`, `account_id`, `connection_id`, `currency`

Shows past valuations captured when syncs succeeded.

---

### 7.7 Transactions

```http
GET /api/v1/portfolio/transactions
Authorization: Bearer <access_token>
```

Only filled when a connector can import trades reliably. Afrinvestor currently focuses on holdings + cash; this may be empty.

---

## 7A. Deposits — dedicated funding account

Live call (authenticates to the broker). Returns virtual-account details only; does **not** initiate a payment.

```http
GET /api/v1/deposits/instructions?connection_id=12
Authorization: Bearer <access_token>
```

```json
{
  "connection_id": 12,
  "provider_id": "afrinvestor",
  "bank_name": "MONIEPOINT",
  "account_number": "1036495647",
  "account_name": "SOJ",
  "currency": "NGN",
  "bank_code": "50515",
  "provider_reference": "50515",
  "raw": {"bank_name": "MONIEPOINT", "account_number": "1036495647"}
}
```

Unsupported providers return `501 CONNECTOR_NOT_IMPLEMENTED`.

---

## 7A2. Markets — what you can buy

List tradeable markets and instruments on a connection (live broker call).

```http
GET /api/v1/markets?connection_id=12
Authorization: Bearer <access_token>
```

```http
GET /api/v1/markets/instruments?connection_id=12&market_id=NGX&search=ACCESS&page=1&per_page=100
Authorization: Bearer <access_token>
```

Afrinvestor currently exposes **NGX** equities through this surface (symbols like `ACCESSCORP`, `GTCO`). Use a returned `symbol` on `POST /api/v1/orders`.

---

## 7B. Orders — buy / sell

Places a live equity order through the broker connector.

```http
POST /api/v1/orders
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "connection_id": 12,
  "side": "buy",
  "symbol": "ACCESSCORP",
  "quantity": "1",
  "order_type": "market",
  "account_external_id": "optional-portfolio-id",
  "pin": "optional-4-digit-pin"
}
```

`side` is `buy` or `sell`. Use `order_type: "limit"` with `limit_price` for limit orders. Unsupported providers return `501`.

---

## 7C. Loan — collateral & eligibility (from DB)

Loan endpoints **only read saved purchased holdings** (market value from sync).
**Cash / deposits are excluded.** Default LTV is **30%**.
Login and new connections auto-enqueue sync so holdings stay upserted. Full detail: [LOAN.md](LOAN.md).

### All collateral

```http
GET /api/v1/loan/collateral
Authorization: Bearer <access_token>
```

Returns every selectable holding (`holding:{id}`), per-provider and per-currency totals, and `grand_total_eligible_loan` when a single currency is present.

### Eligible loan (all holdings)

```http
GET /api/v1/loan/eligibility
Authorization: Bearer <access_token>
```

`eligible_loan = 0.30 × total holding market value` (no cash).

### Eligible loan (selected holdings)

```http
POST /api/v1/loan/eligibility/selected
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "holding_ids": [12, 15],
  "asset_ids": ["holding:12"]
}
```

Returns `0.30 ×` the selected holdings’ stored market values.

---

## 8. Schedules — auto-sync on a timer

All need Bearer token.

### 8.1 Create schedule

```http
POST /api/v1/schedules
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "connection_id": 12,
  "schedule_type": "daily",
  "enabled": true,
  "notes": "Nightly Afrinvestor pull"
}
```

`schedule_type` options:

| Value | Meaning |
|-------|---------|
| `manual` | Stored but not auto-run |
| `hourly` | Every hour |
| `every_6_hours` | Every 6 hours |
| `daily` | Every day |
| `weekly` | Every week |
| `cron` | Custom cron (also send `cron_expression`) |

Cron example:

```json
{
  "connection_id": 12,
  "schedule_type": "cron",
  "cron_expression": "0 6 * * *",
  "enabled": true
}
```

(`0 6 * * *` = 06:00 UTC daily)

### 8.2 List / get / update / delete

```http
GET    /api/v1/schedules
GET    /api/v1/schedules/{schedule_id}
PATCH  /api/v1/schedules/{schedule_id}
DELETE /api/v1/schedules/{schedule_id}
```

---

## 9. How the connector is used (simple story)

### Who writes connectors?

Developers. Each broker gets a folder like:

```text
app/connectors/afrinvestor/   ← operational
app/connectors/morgan/        ← scaffolded (see docs/MORGAN.md)
  connector.py      ← login + fetch
  selectors.py      ← web page selectors
  api_paths.py      ← broker API URLs
  parser.py         ← turn raw data into common format
```

### Registration

```python
@register_connector("afrinvestor")  # or "morgan"
class AfrinvestConnector(...):
    ...
```

On startup the app discovers connectors and lists them at `GET /api/v1/providers`.

### When is a connector used?

Only when you:

- `POST /connections/{id}/test`, or  
- `POST /connections/{id}/sync` (manual or scheduled)

The sync service:

1. Checks you own the connection  
2. Decrypts credentials in memory  
3. Creates the connector for `provider_id`  
4. Calls `authenticate` → `get_accounts` → `get_holdings` → `get_cash_balance`  
5. Saves results  
6. Calls `disconnect()` (closes browser)

Portfolio GET endpoints **do not** open Afrinvestor. They only read MySQL.

### Adding another broker later

1. Copy `example_broker` or follow `docs/ADDING_PROVIDERS.md`  
2. Implement the connector  
3. Restart app  
4. Create a new connection with that `provider_id`  
5. Sync  

Core portfolio APIs stay the same.

---

## 10. Cheat sheet — happy path for Afrinvestor

```text
1. POST /api/v1/auth/register
2. POST /api/v1/auth/login                    → save access_token
3. GET  /api/v1/providers/afrinvestor         → see required fields
4. POST /api/v1/connections                   → save connection_id
5. POST /api/v1/connections/{id}/sync         → save sync_id
6. GET  /api/v1/syncs/{sync_id}               → wait until completed
7. GET  /api/v1/portfolio/summary             → totals + cash
8. GET  /api/v1/portfolio/accounts            → account + cash_balance
9. GET  /api/v1/portfolio/holdings            → ACCESSCORP, qty, P/L…
```

Every step after login includes:

```http
Authorization: Bearer <access_token>
```

---

## 11. Full endpoint list

| Method | Path | Auth? | Purpose |
|--------|------|-------|---------|
| GET | `/health` | No | Is server up? |
| POST | `/api/v1/auth/register` | No | Create app user |
| POST | `/api/v1/auth/login` | No | Get tokens |
| POST | `/api/v1/auth/refresh` | No | Renew tokens |
| POST | `/api/v1/auth/logout` | Yes | Revoke session |
| GET | `/api/v1/auth/me` | Yes | Current user |
| GET | `/api/v1/providers` | No | List brokers |
| GET | `/api/v1/providers/{id}` | No | One broker metadata |
| POST | `/api/v1/connections` | Yes | Save broker credentials |
| GET | `/api/v1/connections` | Yes | List connections |
| GET | `/api/v1/connections/{id}` | Yes | One connection |
| PATCH | `/api/v1/connections/{id}` | Yes | Update connection |
| DELETE | `/api/v1/connections/{id}` | Yes | Delete connection |
| POST | `/api/v1/connections/{id}/test` | Yes | Quick connectivity test |
| POST | `/api/v1/connections/{id}/mfa` | Yes | Submit OTP |
| POST | `/api/v1/connections/{id}/sync` | Yes | Start sync (`202`) |
| GET | `/api/v1/connections/{id}/syncs` | Yes | Sync history for connection |
| GET | `/api/v1/syncs` | Yes | All your syncs |
| GET | `/api/v1/syncs/{id}` | Yes | Sync status |
| POST | `/api/v1/syncs/{id}/cancel` | Yes | Cancel sync |
| GET | `/api/v1/portfolio/summary` | Yes | Totals + cash |
| GET | `/api/v1/portfolio/holdings` | Yes | Positions |
| GET | `/api/v1/portfolio/accounts` | Yes | Accounts |
| GET | `/api/v1/portfolio/by-broker` | Yes | Split by broker |
| GET | `/api/v1/portfolio/by-security` | Yes | Merge same security |
| GET | `/api/v1/portfolio/history` | Yes | Snapshots |
| GET | `/api/v1/portfolio/transactions` | Yes | Trades (if available) |
| GET | `/api/v1/deposits/instructions` | Yes | Dedicated bank / VA funding details (`?connection_id=`) |
| GET | `/api/v1/markets` | Yes | Tradeable markets for a connection |
| GET | `/api/v1/markets/instruments` | Yes | Search/list buyable instruments |
| POST | `/api/v1/orders` | Yes | Place buy/sell order on a connection |
| GET | `/api/v1/loan/collateral` | Yes | Purchased holdings + totals (cash excluded) |
| GET | `/api/v1/loan/eligibility` | Yes | Eligible loan = 30% of all holding values |
| POST | `/api/v1/loan/eligibility/selected` | Yes | Eligible loan = 30% of selected holdings |
| GET | `/api/v1/schedules` | Yes | List schedules |
| GET | `/api/v1/schedules/{id}` | Yes | One schedule |
| POST | `/api/v1/schedules` | Yes | Create schedule |
| PATCH | `/api/v1/schedules/{id}` | Yes | Update schedule |
| DELETE | `/api/v1/schedules/{id}` | Yes | Delete schedule |

---

## 12. Common mistakes

| Mistake | Fix |
|---------|-----|
| Call holdings before sync | Sync first; portfolio reads DB |
| Use Afrinvestor password on `/auth/login` | App login ≠ broker login |
| Forget `Authorization` header | Add `Bearer <access_token>` |
| Expect password in connection GET | Secrets are never returned |
| Think portfolio GET logs into Afrinvestor | Only sync/test use the connector |
| Add NGN + USD in your own code | App keeps currencies separate on purpose |

---

## 13. Related docs

- Setup / MySQL / keys: [README.md](../README.md)
- Afrinvestor connector details: [AFRINVESTOR.md](AFRINVESTOR.md)
- Loan collateral / eligibility: [LOAN.md](LOAN.md)
- Add a new broker: [ADDING_PROVIDERS.md](ADDING_PROVIDERS.md)
