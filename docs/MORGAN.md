# Morgan Capital Connector Status

**Implementation status: `operational`** (live-verified 2026-10-29 — modern app only)

## Platform

- Base: `https://www.morgancapitalgroup.com`
- Sign-in: email + password + **Cloudflare Turnstile**
- Session cookie: `mc_session`
- Auth call: Next.js server action `signIn` (`Next-Action: 7007449683358a67772b2bea04983b29fdd55abe1f`)
- Data surface: authenticated HTML/RSC pages (`/dashboard`, `/portfolio`, `/wallet`, `/markets`, `/orders`) — **no** REST `/api/*` (those 404)

Classic iTrade is **not** used by this connector.

## Production session lifecycle

1. **Bootstrap (rare):** SeleniumBase UC Mode solves Turnstile.
   - Linux servers: `xvfb=True` (virtual display) — **not** Chrome `headless=True` (Turnstile fails).
   - Windows / local: headed Chrome.
2. **Persist:** Playwright `storage_state` (cookies) is returned as `AuthResult.options_patch` and merged into **encrypted** connection options. Never exposed on API responses.
3. **Reuse (hot path):** Sync / deposits / markets / orders open **headless** Playwright with the stored state — no interactive browser.
4. **Refresh:** Successful reuse re-exports cookies into `options_patch`. Expired state (redirect to `/sign-in`) triggers UC bootstrap again.

Connection list includes non-secret `has_broker_session` when encrypted cookies are present.

## Capabilities

| Capability | Behaviour |
|------------|-----------|
| Portfolio sync | DOM scrape; empty holdings confirmed as `[]` |
| Cash | Wallet page `AVAILABLE BALANCE` |
| Markets / instruments | Scrape `/markets` gainers/losers/table |
| Deposit instructions | Virtual account on wallet — **only after KYC** (returns 501 until then) |
| Orders | Stock pages under `/markets/{SYMBOL}`; trading locked until KYC (501) |

## Auth options (connection `options`, encrypted)

| Key | Purpose |
|-----|---------|
| `storage_state` | Playwright cookie jar (managed by the connector) |
| `auth_mode` | `uc` (default) or `cdp` |
| `scrape_headless` / `headless` | Headless Playwright after login (default `true`) |
| `uc_headed` / `uc_headless` | Override UC display mode |
| `captcha_timeout_sec` | Turnstile wait (default 180) |

```powershell
# Manual UC login / refresh local debug state (optional)
$env:MORGAN_USER='you@email.com'
$env:MORGAN_PASS='...'
python scripts\morgan_uc_login.py

# Full /api/v1 flow (UC once → encrypted state → headless reuse)
python scripts\record_morgan_api_flow.py
```

## Observed account caveats

- KYC incomplete → banner: unlock trading / IPOs / withdrawals
- Virtual account: *issued the moment KYC is approved*
- Wallet powered by **Wema** (NDIC insured)

Never commit credentials or `logs/morgan_storage_state.json`.
