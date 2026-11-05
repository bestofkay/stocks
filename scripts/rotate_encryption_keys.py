#!/usr/bin/env python3
"""Re-encrypt broker credentials after rotating APP_ENCRYPTION_KEY.

Procedure:
  1. Set APP_ENCRYPTION_KEY to the NEW key and bump encryption_key_version in config
     (or keep version logic consistent).
  2. This script currently requires decrypt with the CURRENT key — so run it
     BEFORE replacing APP_ENCRYPTION_KEY, OR extend Settings with
     APP_ENCRYPTION_KEY_PREVIOUS for dual-key decrypt.

  Minimal safe workflow for v1:
    a. Keep current APP_ENCRYPTION_KEY
    b. Generate new_key = python scripts/generate_keys.py
    c. Export OLD_KEY=$APP_ENCRYPTION_KEY NEW_KEY=<new>
    d. Run this script with both env vars set
    e. Update .env APP_ENCRYPTION_KEY to NEW_KEY
    f. Restart the app

Usage:
    set OLD_ENCRYPTION_KEY=...
    set NEW_ENCRYPTION_KEY=...
    python scripts/rotate_encryption_keys.py
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.encryption import EncryptionService, EncryptedEnvelope  # noqa: E402
from app.db.database import init_db, session_scope  # noqa: E402
from app.db.models import BrokerConnection, Provider  # noqa: E402


def _service_from_b64(key_b64: str, version: int) -> EncryptionService:
    class _S:
        encryption_key_bytes = base64.b64decode(key_b64)
        encryption_key_version = version

        @property
        def app_encryption_key(self):
            return key_b64

    return EncryptionService(settings=_S())  # type: ignore[arg-type]


def main() -> None:
    old_key = os.environ.get("OLD_ENCRYPTION_KEY")
    new_key = os.environ.get("NEW_ENCRYPTION_KEY")
    if not old_key or not new_key:
        print("Set OLD_ENCRYPTION_KEY and NEW_ENCRYPTION_KEY environment variables.", file=sys.stderr)
        sys.exit(1)

    old_svc = _service_from_b64(old_key, version=1)
    new_svc = _service_from_b64(new_key, version=2)

    # Force settings load for DB URL
    os.environ.setdefault("APP_ENCRYPTION_KEY", old_key)
    if "APP_SECRET_KEY" not in os.environ:
        os.environ["APP_SECRET_KEY"] = "temporary-rotation-secret-key-32chars!!"

    init_db()
    count = 0
    with session_scope() as db:
        connections = db.query(BrokerConnection).all()
        for conn in connections:
            provider = db.get(Provider, conn.provider_id)
            if provider is None:
                continue
            aad = f"user:{conn.user_id}:provider:{provider.provider_key}".encode("utf-8")
            # Temporarily allow decrypt with old key version
            envelope = EncryptedEnvelope.from_json(conn.credentials_encrypted)
            # Patch old service key version to match envelope
            old_svc._key_version = envelope.key_version  # noqa: SLF001
            plaintext = old_svc.decrypt_dict(conn.credentials_encrypted, aad=aad)
            conn.credentials_encrypted = new_svc.encrypt_dict(plaintext, aad=aad)
            if conn.options_encrypted:
                old_svc._key_version = EncryptedEnvelope.from_json(conn.options_encrypted).key_version  # noqa: SLF001
                opts = old_svc.decrypt_dict(conn.options_encrypted, aad=aad + b":options")
                conn.options_encrypted = new_svc.encrypt_dict(opts, aad=aad + b":options")
            if conn.session_state_encrypted:
                old_svc._key_version = EncryptedEnvelope.from_json(conn.session_state_encrypted).key_version  # noqa: SLF001
                sess = old_svc.decrypt_dict(conn.session_state_encrypted, aad=aad + b":session")
                conn.session_state_encrypted = new_svc.encrypt_dict(sess, aad=aad + b":session")
            count += 1
            plaintext.clear()
    print(f"Re-encrypted {count} connection(s). Update APP_ENCRYPTION_KEY and restart.")


if __name__ == "__main__":
    main()
