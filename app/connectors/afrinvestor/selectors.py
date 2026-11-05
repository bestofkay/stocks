"""Approved domains and UI selectors for Afrinvestor.

IMPORTANT: Selectors below are derived from publicly observable page structure
at https://afrinvestor.com/home (login form with Email / Password / Sign in).

Post-login portfolio selectors are NOT verified against a live authenticated
session. They are placeholders marked UNVERIFIED. Do not treat this connector
as operational until integration testing confirms them.

Update this file only after inspecting the live authenticated UI.
"""

from __future__ import annotations

# Strict allow-list — browser must not navigate outside these hosts
APPROVED_DOMAINS: frozenset[str] = frozenset(
    {
        "afrinvestor.com",
        "www.afrinvestor.com",
    }
)

# Live site redirects /home -> /auth; form is on /auth (verified 2026-10-27)
LOGIN_URL = "https://afrinvestor.com/auth"
BASE_URL = "https://afrinvestor.com"

# --- Observed on https://afrinvestor.com/auth (2026-10-27) ---
# Labels: "Email", "Password"; inputs type=email / type=password (no name/id);
# submit button text "Sign in"; cookie banner button "Got it!"
LOGIN_EMAIL_SELECTORS = [
    'input[type="email"]',
    'label:has-text("Email") >> xpath=following::input[1]',
    'input[name="email"]',
    'input[placeholder*="Email" i]',
    'input[autocomplete="email"]',
]

LOGIN_PASSWORD_SELECTORS = [
    'input[type="password"]',
    'label:has-text("Password") >> xpath=following::input[1]',
    'input[name="password"]',
    'input[placeholder*="Password" i]',
    'input[autocomplete="current-password"]',
]

LOGIN_SUBMIT_SELECTORS = [
    'button:has-text("Sign in")',
    'button[type="submit"]',
    'input[type="submit"]',
]

COOKIE_DISMISS_SELECTORS = [
    'button:has-text("Got it!")',
    'button:has-text("Accept")',
    'button:has-text("I agree")',
]

# Success indicators — verified against live authenticated /home (2026-10-27)
AUTH_SUCCESS_INDICATORS = [
    'text=Total Portfolio Value',
    'text=Total Wallet Value',
    'a:has-text("Portfolio")',
    'a:has-text("Wallets")',
    'a:has-text("Invest")',
]

# Keep failure matchers specific — broad words like "failed" false-positive on market copy
AUTH_FAILURE_INDICATORS = [
    'text=Invalid email',
    'text=Invalid password',
    'text=Incorrect email',
    'text=Incorrect password',
    'text=Wrong password',
    'text=Login failed',
    'text=Authentication failed',
    'text=Unable to sign in',
]

# MFA / OTP — Afrinvest docs mention OTP for password reset; login MFA unknown.
# Detect common patterns without inventing a specific flow.
MFA_INDICATORS = [
    'input[name*="otp" i]',
    'input[placeholder*="OTP" i]',
    'input[placeholder*="verification" i]',
    'text=Enter OTP',
    'text=verification code',
    'text=One Time Password',
]

CAPTCHA_INDICATORS = [
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    '.g-recaptcha',
    '#captcha',
]

# Verified: sidebar uses <button>Portfolio</button> -> /portfolio
PORTFOLIO_NAV_SELECTORS = [
    'button:has-text("Portfolio")',
    'text=Portfolio',
    'a:has-text("Portfolio")',
    'a[href*="/portfolio"]',
]

# UNVERIFIED — holdings table structure unknown without authenticated access
HOLDINGS_CONTAINER_SELECTORS = [
    '[data-testid="holdings"]',
    'table',
    '[class*="holding" i]',
    '[class*="portfolio" i]',
]

HOLDINGS_ROW_SELECTORS = [
    'table tbody tr',
    '[data-testid="holding-row"]',
    '[class*="holding-row" i]',
]

CASH_BALANCE_SELECTORS = [
    'text=/wallet|cash|available/i',
    '[data-testid="cash-balance"]',
    '[class*="wallet" i]',
]

LOGOUT_SELECTORS = [
    'button:has-text("Log out")',
    'button:has-text("Logout")',
    'a:has-text("Log out")',
    'a:has-text("Sign out")',
]

INTEGRATION_BLOCKERS = [
    "No public official API docs; portfolio/deposit/order paths observed from authorized sessions.",
    "Money-market / fixed-deposit holdings are not imported (nets valuations only).",
    "Trading PIN may be required for some accounts; pass pin on POST /orders when needed.",
]
