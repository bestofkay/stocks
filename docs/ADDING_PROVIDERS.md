# Adding a New Broker Provider

New providers require **only** a connector package, tests, and registration. Do not modify portfolio, auth, sync, or database core services for provider-specific logic.

## Steps

1. Create package:

```text
app/connectors/mybroker/
  __init__.py
  connector.py
  selectors.py      # browser connectors
  parser.py
```

2. Implement `BrokerConnector` and decorate:

```python
from app.connectors.base import BrokerConnector
from app.connectors.registry import register_connector

@register_connector("mybroker")
class MyBrokerConnector(BrokerConnector):
    @classmethod
    def metadata(cls) -> ProviderMetadata:
        ...

    async def authenticate(self, credentials, options=None):
        ...

    async def get_accounts(self):
        ...

    async def get_holdings(self, account):
        ...

    async def disconnect(self):
        ...
```

3. Set `implementation_status` honestly:
   - `registered` — stub only
   - `scaffolded` — structure + partial inspection
   - `operational` — verified with integration tests

4. Add unit tests with HTML/JSON fixtures (no live credentials).
5. Add `scripts/record_<id>_flow.py` (read-only live check; credentials from env vars,
   redacted transcript to `logs/`). Promote to `operational` only after it passes.
6. Restart the app — providers sync into MySQL on startup via discovery.
7. Confirm `GET /api/v1/providers/mybroker` returns metadata.

## Shared platforms

If a new broker runs software already supported (e.g. Zanibal E-Business Suite / "IW" portals), subclass the
shared base (`app/connectors/zanibal_iw/base.py`) instead of writing a new connector.

## Rules

- Approved domains only for browser automation.
- Never invent undocumented APIs.
- Never return secrets in API responses or logs.
- Portfolio sync is read-only by default. Optional write capabilities (`deposit_instructions`, `orders`) must use observed provider APIs only — never invent endpoints. Do not implement withdrawals/transfers unless explicitly scoped.
- Arbitrary user-uploaded connector code is not allowed.

## Current connectors

| Provider id | Status | Notes |
|-------------|--------|-------|
| `afrinvestor` | operational | Hybrid Playwright + observed APIs |
| `morgan` | operational | Modern app (Turnstile + DOM scrape); see [MORGAN.md](MORGAN.md) |
| `cardinalstone` | operational | Authenticated HTTP (Bearer JWT, no browser); see [CARDINALSTONE.md](CARDINALSTONE.md) |
| `vetiva` | scaffolded | Authenticated HTTP (Bearer JWT, no browser); see [VETIVA.md](VETIVA.md) |
| `wstc` | scaffolded | Zanibal E-Business Suite subclass; see [ZANIBAL_IW.md](ZANIBAL_IW.md) |
| `sisl` | scaffolded | Zanibal E-Business Suite subclass; see [ZANIBAL_IW.md](ZANIBAL_IW.md) |
| `cordros` | scaffolded | Authenticated HTTP via SymPlus gateway; see [CORDROS.md](CORDROS.md) |
| `investnow` | scaffolded | Browser-driven (site encrypts login fields; never re-implemented); see [INVESTNOW.md](INVESTNOW.md) |
| `example_broker` | registered | Template only |
