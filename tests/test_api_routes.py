"""API route wiring and auth/authorization tests with dependency overrides."""

from __future__ import annotations

import base64
import os
import secrets
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault(
    "APP_ENCRYPTION_KEY",
    base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
)
os.environ.setdefault("APP_SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault(
    "DATABASE_URL",
    "mysql+pymysql://portfolio_user:change_me@127.0.0.1:3306/portfolio_test",
)
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SCHEDULER_ENABLED", "false")

from app.api.deps import get_current_user, get_db  # noqa: E402
from app.core.config import clear_settings_cache  # noqa: E402
from app.core.errors import AppError, ErrorCode  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
def user():
    return SimpleNamespace(
        id=1,
        email="owner@example.com",
        is_active=True,
        is_admin=True,
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def other_user():
    return SimpleNamespace(
        id=2,
        email="other@example.com",
        is_active=True,
        is_admin=False,
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def client(user):
    clear_settings_cache()
    app = create_app(testing=True)

    def _db():
        yield MagicMock()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_list_providers(client):
    r = client.get("/api/v1/providers")
    assert r.status_code == 200
    body = r.json()
    ids = {p["provider_id"] for p in body["providers"]}
    assert "afrinvestor" in ids
    assert "example_broker" in ids
    afr = next(p for p in body["providers"] if p["provider_id"] == "afrinvestor")
    assert afr["operational"] is True
    assert afr["implementation_status"] == "operational"
    example = next(p for p in body["providers"] if p["provider_id"] == "example_broker")
    assert example["operational"] is False


def test_get_provider_not_found(client):
    r = client.get("/api/v1/providers/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == ErrorCode.PROVIDER_NOT_FOUND.value


def test_security_headers(client):
    r = client.get("/health")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"


def test_me(client, user):
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == user.email


def test_unauthenticated_me_rejected():
    clear_settings_cache()
    app = create_app(testing=True)
    # No auth override — dependency runs for real and should 401
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/v1/auth/me")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_connection_ownership_isolation(user, other_user):
    clear_settings_cache()
    app = create_app(testing=True)
    db = MagicMock()

    def _db():
        yield db

    from app.services import connection_service

    original = connection_service.get_connection

    def fake_get(db_sess, current, connection_id):
        if current.id != user.id:
            raise AppError(
                ErrorCode.CONNECTION_NOT_FOUND,
                "Connection not found",
                status_code=404,
            )
        return MagicMock(
            id=connection_id,
            user_id=user.id,
            provider_id=1,
            display_name="x",
            status="configured",
            last_sync_at=None,
            last_successful_sync_at=None,
            last_error_code=None,
            credentials_encrypted="enc",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    connection_service.get_connection = fake_get  # type: ignore
    connection_service.connection_to_response = lambda _db, conn: {  # type: ignore
        "id": conn.id,
        "provider_id": "afrinvestor",
        "provider_key": "afrinvestor",
        "display_name": conn.display_name,
        "status": conn.status,
        "last_sync_at": None,
        "last_successful_sync_at": None,
        "last_error_code": None,
        "has_credentials": True,
        "created_at": conn.created_at,
        "updated_at": conn.updated_at,
    }

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: other_user
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/api/v1/connections/12")
            assert r.status_code == 404
            assert r.json()["error"]["code"] == "CONNECTION_NOT_FOUND"
    finally:
        connection_service.get_connection = original  # type: ignore
        app.dependency_overrides.clear()
