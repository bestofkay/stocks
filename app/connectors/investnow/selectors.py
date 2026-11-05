"""InvestNow (United Capital) - approved domains, pages and UI selectors.

Observed 2026-11-03 on https://app.investnow.ng (Next.js). Login is two-step:
email -> "Continue" -> password. The site encrypts login fields in the browser
before sending them, so this connector drives the real login form in Playwright
and lets the site's own code do that; it never re-implements the encryption.
Data is read by capturing the JSON the app itself loads from api.investnow.ng.
"""

from __future__ import annotations

APP = "https://app.investnow.ng"
API_HOST = "api.investnow.ng"
APPROVED_DOMAINS: frozenset[str] = frozenset({"app.investnow.ng", "api.investnow.ng"})

LOGIN_URL = f"{APP}/login"
PORTFOLIO_URL = f"{APP}/portfolio"
DASHBOARD_URL = f"{APP}/dashboard"

# API paths the app loads on those pages (captured passively, never called directly)
PATH_CUSTOMER = "/api/v1/customers/get"
PATH_PORTFOLIO_ITEMS = "/api/v2/portfolios/get-portfolio-items"
PATH_INVOICES = "/api/v1/accounts/get-invoice-history"
PATH_WITHDRAWALS = "/api/v1/portfolios/get-withdrawal-requests"

EMAIL_SELECTORS = ['input[type="email"]', 'input[name="email"]']
CONTINUE_SELECTORS = ['button:has-text("Continue")', 'button[type="submit"]']
PASSWORD_SELECTORS = ['input[type="password"]', 'input[name="password"]']
LOGIN_SUBMIT_SELECTORS = ['button:has-text("Login")', 'button:has-text("Log in")', 'button:has-text("Continue")',
                          'button[type="submit"]']

AUTH_SUCCESS_URL_PARTS = ("/dashboard", "/portfolio", "/products")
AUTH_FAILURE_TEXT = ["Invalid", "incorrect", "not found", "does not exist", "wrong password"]
OTP_SELECTORS = ['input[autocomplete="one-time-code"]', 'input[name*="otp" i]', 'input[placeholder*="OTP" i]']
# Imperva/Incapsula challenge or block page - never bypassed; reported as captcha_required
BOT_CHALLENGE_TEXT = ["Incapsula incident", "Request unsuccessful", "Access denied", "verify you are human"]

INTEGRATION_BLOCKERS = [
    "Browser-driven (Playwright): needs a browser on the sync machine; Imperva may challenge headless runs "
    "(reported as captcha_required, never bypassed).",
    "Stock holdings and balances stream over a WebSocket from a separate trading backend; only portfolio-level "
    "data from get-portfolio-items is imported.",
    "Trading happens on the separate trade.investnow.ng platform, so orders and instruments are not supported here.",
    "Deposits are per-payment invoices (no static virtual account observed), so deposit instructions are not offered.",
]
