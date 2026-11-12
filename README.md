# Portfolio Aggregator

Backend-only multi-broker investment portfolio aggregation service.

Connects to broker APIs and web portals, encrypts credentials, synchronizes holdings into MySQL, and exposes a unified portfolio over REST (FastAPI + Swagger).

**No frontend.** Manage everything via REST, scripts, or `/docs`.

## Stack

- Python 3.12+
- FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic
- MySQL 8+
- Playwright (browser connectors), httpx (API connectors)
- AES-256-GCM credential encryption, Argon2id app passwords
- APScheduler (no Redis/Celery)

## Quick start

### 1. Virtual environment

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

**Linux / macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. MySQL

```bash
mysql -u root -p < scripts/create_mysql_db.sql
```

Change the default password in SQL and in `.env` before any real use.

### 3. Configuration

```bash
copy .env.example .env   # Windows
# cp .env.example .env   # Linux/macOS

python scripts/generate_keys.py
```

Paste `APP_ENCRYPTION_KEY` and `APP_SECRET_KEY` into `.env`. **Back up the encryption key offline** — losing it makes stored broker credentials unrecoverable.

Optional one-time admin bootstrap (if no users exist):

```env
BOOTSTRAP_ADMIN_EMAIL=admin@example.com
BOOTSTRAP_ADMIN_PASSWORD=choose-a-long-password
```

Clear these after first login. Alternatively register via `POST /api/v1/auth/register` (first user becomes admin).

### 4. Migrate and run

```bash
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- Swagger: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc
- Health: http://127.0.0.1:8000/health

## Core API

**Beginner walkthrough (all endpoints, start → finish, how connectors work):**  
[docs/API_GUIDE.md](docs/API_GUIDE.md)

| Area | Endpoints |
|------|-----------|
| Auth | `POST /api/v1/auth/register`, `/login`, `/refresh`, `/logout`, `GET /me` |
| Providers | `GET /api/v1/providers`, `GET /api/v1/providers/{id}` |
| Connections | CRUD + `/ready` + `/test` + `/mfa` + `/sync` under `/api/v1/connections` (create syncs by default) |
| Syncs | `GET /api/v1/syncs`, `GET /api/v1/syncs/{id}`, `POST /api/v1/syncs/{id}/cancel` |
| Portfolio | `/summary`, `/holdings`, `/accounts`, `/by-broker`, `/by-security`, `/history`, `/transactions` |
| Loan | `GET /loan/collateral`, `GET /loan/eligibility`, `POST /loan/eligibility/selected` (30% LTV from holdings only; cash excluded); see [docs/LOAN.md](docs/LOAN.md) |
| Schedules | CRUD + `GET /{id}` under `/api/v1/schedules` |

Send `Authorization: Bearer <access_token>` (from login) on protected routes.

## Providers

Connectors are discovered from `app/connectors/*/connector.py`.

| Provider | Status | Type |
|----------|--------|------|
| `afrinvestor` | **operational** (live-verified) | Hybrid (Playwright login + APIs) |
| `morgan` | **operational** (live-verified) | Morgan Capital modern app (Turnstile + DOM); see [docs/MORGAN.md](docs/MORGAN.md) |
| `cardinalstone` | **operational** (live-verified) | Authenticated HTTP (Bearer JWT); see [docs/CARDINALSTONE.md](docs/CARDINALSTONE.md) |
| `investnaija` | **operational** (live-verified; order create gated by broker gateway) | Authenticated HTTP (JWT + uuid); see [docs/INVESTNAIJA.md](docs/INVESTNAIJA.md) |
| `fundvine` | **operational** (live-verified; Paystack funding / EqtyMandate orders) | Authenticated HTTP (NaYa OMS cookies + ASMX); see [docs/FUNDVINE.md](docs/FUNDVINE.md) |
| `iconstock` | **operational** (live-verified; Paystack funding / EqtyMandate orders) | Authenticated HTTP (NaYa OMS cookies + ASMX); see [docs/ICONSTOCK.md](docs/ICONSTOCK.md) |
| `atlas` | **operational** (live-verified; Folio WebSocket dashboard / markets) | Authenticated HTTP + WebSocket (Caladata Folio CitiTrader); see [docs/ATLAS.md](docs/ATLAS.md) |
| `vetiva` | **scaffolded** (endpoints live-observed; pending live run) | Authenticated HTTP (Bearer JWT); see [docs/VETIVA.md](docs/VETIVA.md) |
| `wstc` | **scaffolded** (pages live-observed; pending live run) | Zanibal E-Business Suite (HTML); see [docs/ZANIBAL_IW.md](docs/ZANIBAL_IW.md) |
| `sisl` | **scaffolded** (same software as WSTC; login page inspected) | Zanibal E-Business Suite (HTML); see [docs/ZANIBAL_IW.md](docs/ZANIBAL_IW.md) |
| `cordros` | **scaffolded** (live replay 10/10; portfolio needs KYC) | Authenticated HTTP (Bearer JWT, SymPlus gateway); see [docs/CORDROS.md](docs/CORDROS.md) |
| `investnow` | **scaffolded** (browser-driven; funds/wealth/trust, no stocks/orders) | Playwright login + captured app JSON; see [docs/INVESTNOW.md](docs/INVESTNOW.md) |
| `example_broker` | **registered** (template only) | Authenticated HTTP |

See [docs/AFRINVESTOR.md](docs/AFRINVESTOR.md) for inspection notes and the live verification procedure.  
See [docs/ADDING_PROVIDERS.md](docs/ADDING_PROVIDERS.md) to add brokers without changing core services.

`operational=false` means the connector is registered but **not** claimed as production-verified.

## Security highlights

- Broker credentials encrypted with AES-256-GCM; key from `APP_ENCRYPTION_KEY` only
- Application passwords hashed with Argon2id; revocable signed sessions
- Per-user resource authorization
- Auth rate limiting, request size limits, security headers
- Browser navigation restricted to provider allow-lists (SSRF protection)
- Secrets redacted from logs and audit metadata
- Portfolio sync remains read-only by default; deposits/orders are available only when a connector advertises those capabilities (Afrinvestor, CardinalStone, InvestNaija, Fundvine: funding instructions + equity buy/sell). No withdrawals/transfers.

## Scheduling

APScheduler runs in-process when `SCHEDULER_ENABLED=true`. A MySQL `GET_LOCK` ensures only one worker runs the scheduler. For multi-worker deployments, enable the scheduler on **one** process only, or rely on the lock.

**Limitation:** background sync jobs are in-process. They do not survive process restarts and are not shared across workers. Clients should poll `GET /api/v1/syncs/{id}` after `202` responses.

## Production

1. Set `APP_ENV=production`.
2. Terminate TLS at a reverse proxy (nginx, Caddy, IIS ARR) and forward to `127.0.0.1:8000`.
3. Keep `.env` and encryption keys off disk shared with untrusted users; prefer OS secret storage.
4. Use a strong MySQL password and restrict network access to localhost / private network.
5. Run a single uvicorn worker if using in-process sync + scheduler, **or** document that only one worker enables `SCHEDULER_ENABLED`.

Example nginx location:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

## Tests

Unit tests (no MySQL / no live brokers):

```bash
pytest -q
```

Integration tests require a dedicated MySQL database (`portfolio_test`) and are marked `@pytest.mark.integration`.

Live provider checks (`scripts/record_*_flow.py`) must be opt-in with real credentials and are **not** run by default.

## Key rotation

See `scripts/rotate_encryption_keys.py` and comments in `app/core/encryption.py`.

## Project layout

```text
app/
  api/routes/     # FastAPI routers
  core/           # config, security, encryption, logging
  db/models/      # SQLAlchemy models
  connectors/     # Provider plugins (afrinvestor, ...)
  schemas/        # Pydantic API schemas
  services/       # Business logic
  main.py
migrations/
tests/
scripts/
docs/
```
