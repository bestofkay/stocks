"""Approved domains and UI selectors for Morgan Capital modern app.

Live-verified 2026-10-29 against https://www.morgancapitalgroup.com
(session cookie ``mc_session`` after Turnstile + Next.js ``signIn``).
"""

from __future__ import annotations

APPROVED_DOMAINS: frozenset[str] = frozenset(
    {
        "morgancapitalgroup.com",
        "www.morgancapitalgroup.com",
        "challenges.cloudflare.com",  # Turnstile only
    }
)

MODERN_EMAIL_SELECTORS = [
    'input[type="email"]',
    'input[placeholder*="email" i]',
    'input[placeholder*="example.com" i]',
]
MODERN_PASSWORD_SELECTORS = [
    'input[type="password"]',
    'input[placeholder*="password" i]',
]
MODERN_SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'button:has-text("Sign in")',
]

AUTH_SUCCESS_INDICATORS = [
    'text=/Investor Portal/i',
    'text=/Sign out/i',
    'text=/Dashboard/i',
    'a:has-text("Portfolio")',
    'a:has-text("Wallet")',
    'a:has-text("Markets")',
]

AUTH_FAILURE_INDICATORS = [
    'text=/Invalid email/i',
    'text=/Invalid password/i',
    'text=/Incorrect/i',
    'text=/captcha protection/i',
    'text=/Authentication failed/i',
]

CAPTCHA_INDICATORS = [
    'iframe[src*="challenges.cloudflare.com"]',
    'input[name="cf-turnstile-response"]',
    'text=/Verify you are human/i',
]

INTEGRATION_BLOCKERS = [
    "Sign-in requires Cloudflare Turnstile; connector uses headed Chrome + CDP and may need a manual checkbox click.",
    "Virtual-account funding details are issued only after KYC approval (observed: VA pending until KYC).",
    "Equity trading / withdrawals are locked until KYC is complete (banner observed on dashboard).",
]
