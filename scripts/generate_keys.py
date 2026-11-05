#!/usr/bin/env python3
"""Generate APP_ENCRYPTION_KEY and APP_SECRET_KEY for .env.

Usage:
    python scripts/generate_keys.py

Backup the encryption key securely. Losing it makes broker credentials
irrecoverable. Do not commit keys to version control.
"""

from __future__ import annotations

import base64
import secrets


def main() -> None:
    encryption_key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    secret_key = secrets.token_urlsafe(48)
    print("# Add these to your .env file (never commit .env)")
    print(f"APP_ENCRYPTION_KEY={encryption_key}")
    print(f"APP_SECRET_KEY={secret_key}")
    print()
    print("# Backup APP_ENCRYPTION_KEY offline. Key loss = permanent credential loss.")


if __name__ == "__main__":
    main()
