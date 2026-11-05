"""Access/refresh token rotation tests."""

from __future__ import annotations

import base64
import secrets
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import SessionTokenService, hash_token, utcnow
from app.services import auth_service


def _settings() -> Settings:
    return Settings(
        database_url="mysql+pymysql://u:p@127.0.0.1:3306/db",
        app_encryption_key=base64.b64encode(secrets.token_bytes(32)).decode(),
        app_secret_key="refresh-test-secret-key-at-least-32-chars!",
        app_env="test",
        scheduler_enabled=False,
    )


def test_refresh_rotates_token(monkeypatch):
    settings = _settings()
    token_svc = SessionTokenService(settings)
    monkeypatch.setattr(auth_service, "get_settings", lambda: settings)
    monkeypatch.setattr(auth_service, "get_session_token_service", lambda: token_svc)

    user = SimpleNamespace(id=1, is_active=True)
    session = MagicMock()
    session.session_id = "sid-abc"
    session.revoked_at = None
    session.expires_at = utcnow() + timedelta(days=1)
    refresh = token_svc.create_refresh_token(1, "sid-abc")
    session.refresh_token_hash = hash_token(refresh)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = session
    db.get.return_value = user

    access2, refresh2, out_user = auth_service.refresh_session(db, refresh)
    assert out_user is user
    assert access2
    assert refresh2 != refresh
    assert session.refresh_token_hash == hash_token(refresh2)
    payload = token_svc.verify_access_token(access2)
    assert payload["uid"] == 1


def test_refresh_reuse_revokes(monkeypatch):
    settings = _settings()
    token_svc = SessionTokenService(settings)
    monkeypatch.setattr(auth_service, "get_settings", lambda: settings)
    monkeypatch.setattr(auth_service, "get_session_token_service", lambda: token_svc)

    refresh = token_svc.create_refresh_token(1, "sid-abc")
    session = MagicMock()
    session.session_id = "sid-abc"
    session.revoked_at = None
    session.expires_at = utcnow() + timedelta(days=1)
    session.refresh_token_hash = hash_token("different-token")

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = session

    with pytest.raises(AppError) as exc:
        auth_service.refresh_session(db, refresh)
    assert exc.value.status_code == 401
    assert session.revoked_at is not None
