"""Application password hashing and session tokens."""

from __future__ import annotations

import base64
import secrets

from app.core.config import Settings
from app.core.security import (
    SessionTokenService,
    hash_password,
    needs_rehash,
    verify_password,
)


def test_argon2id_hash_and_verify():
    h = hash_password("correct-horse-battery")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct-horse-battery")
    assert not verify_password(h, "wrong-password")


def test_session_tokens_roundtrip():
    settings = Settings(
        database_url="mysql+pymysql://u:p@127.0.0.1:3306/db",
        app_encryption_key=base64.b64encode(secrets.token_bytes(32)).decode(),
        app_secret_key="session-secret-key-at-least-32-chars!!",
        app_env="test",
    )
    svc = SessionTokenService(settings)
    access = svc.create_access_token(1, "sid123")
    payload = svc.verify_access_token(access)
    assert payload["uid"] == 1
    assert payload["sid"] == "sid123"
    assert payload["typ"] == "access"

    refresh = svc.create_refresh_token(1, "sid123")
    rpayload = svc.verify_refresh_token(refresh)
    assert rpayload["typ"] == "refresh"


def test_needs_rehash_false_for_fresh_hash():
    h = hash_password("another-strong-password")
    assert needs_rehash(h) is False
