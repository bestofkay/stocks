"""Credential encryption unit tests."""

from __future__ import annotations

import base64
import secrets

import pytest

from app.core.config import Settings, clear_settings_cache
from app.core.encryption import EncryptionError, EncryptionService, redact_secrets


def _settings(key: bytes | None = None) -> Settings:
    raw = key or secrets.token_bytes(32)
    return Settings(
        database_url="mysql+pymysql://u:p@127.0.0.1:3306/portfolio_test",
        app_encryption_key=base64.b64encode(raw).decode("ascii"),
        app_secret_key="x" * 48,
        app_env="test",
        scheduler_enabled=False,
    )


def test_encrypt_decrypt_dict_roundtrip():
    svc = EncryptionService(_settings())
    data = {"username": "user@example.com", "password": "s3cret!"}
    envelope = svc.encrypt_dict(data)
    assert "password" not in envelope
    assert "s3cret" not in envelope
    assert svc.decrypt_dict(envelope) == data


def test_encrypt_rejects_empty():
    svc = EncryptionService(_settings())
    with pytest.raises(EncryptionError):
        svc.encrypt_bytes(b"")


def test_decrypt_tampered_ciphertext():
    svc = EncryptionService(_settings())
    envelope = svc.encrypt_dict({"a": 1})
    tampered = envelope[:-4] + "XXXX"
    with pytest.raises(EncryptionError):
        svc.decrypt_dict(tampered)


def test_key_version_mismatch():
    key = secrets.token_bytes(32)
    svc1 = EncryptionService(_settings(key))
    envelope = svc1.encrypt_dict({"x": "y"})
    settings = _settings(key)
    settings.encryption_key_version = 99  # type: ignore[misc]
    # Settings is frozen-ish via pydantic — create new service with bumped version
    class S:
        encryption_key_bytes = key
        encryption_key_version = 99

    svc2 = EncryptionService(settings=S())  # type: ignore[arg-type]
    with pytest.raises(EncryptionError, match="Key version"):
        svc2.decrypt_dict(envelope)


def test_field_aad_binding():
    svc = EncryptionService(_settings())
    enc = svc.encrypt_secret_field("tokensecret", field_name="access_token")
    assert svc.decrypt_secret_field(enc, field_name="access_token") == "tokensecret"
    with pytest.raises(EncryptionError):
        svc.decrypt_secret_field(enc, field_name="password")


def test_redact_secrets():
    data = {
        "username": "alice",
        "password": "secret",
        "nested": {"api_key": "abc", "ok": 1},
        "list": [{"token": "t"}],
    }
    redacted = redact_secrets(data)
    assert redacted["password"] == "[REDACTED]"
    assert redacted["nested"]["api_key"] == "[REDACTED]"
    assert redacted["nested"]["ok"] == 1
    assert redacted["list"][0]["token"] == "[REDACTED]"
    assert redacted["username"] == "alice"


def test_encryption_key_validation():
    clear_settings_cache()
    with pytest.raises(Exception):
        Settings(
            database_url="mysql+pymysql://u:p@127.0.0.1:3306/db",
            app_encryption_key="not-valid-base64!!!",
            app_secret_key="x" * 48,
        )
    with pytest.raises(Exception):
        Settings(
            database_url="mysql+pymysql://u:p@127.0.0.1:3306/db",
            app_encryption_key=base64.b64encode(b"short").decode(),
            app_secret_key="x" * 48,
        )
