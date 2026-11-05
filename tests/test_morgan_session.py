"""Unit tests for Morgan production session helpers + AuthResult options_patch."""

from __future__ import annotations

import base64
import os
import secrets
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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

from app.connectors.models import AuthResult  # noqa: E402
from app.connectors.morgan import session as morgan_session  # noqa: E402
from app.core.config import clear_settings_cache  # noqa: E402
from app.core.encryption import get_encryption_service  # noqa: E402
from app.services.connection_service import (  # noqa: E402
    apply_options_patch,
    connection_has_broker_session,
)


@pytest.fixture(autouse=True)
def _settings():
    clear_settings_cache()
    yield
    clear_settings_cache()


def test_uc_driver_kwargs_linux_uses_xvfb_not_headless():
    with patch.object(morgan_session, "is_linux", return_value=True):
        kw = morgan_session.uc_driver_kwargs()
    assert kw["uc"] is True
    assert kw.get("xvfb") is True
    assert kw.get("headless") is False


def test_uc_driver_kwargs_windows_headed():
    with patch.object(morgan_session, "is_linux", return_value=False):
        kw = morgan_session.uc_driver_kwargs()
    assert kw["uc"] is True
    assert kw.get("headed") is True
    assert kw.get("headless") is False


def test_scrape_headless_defaults_true():
    assert morgan_session.scrape_headless_default({}) is True
    assert morgan_session.scrape_headless_default({"headless": False}) is False
    assert morgan_session.scrape_headless_default({"scrape_headless": False}) is False


def test_cookies_to_storage_state_and_session_detect():
    state = morgan_session.cookies_to_storage_state(
        [
            {
                "name": "mc_session",
                "value": "abc",
                "domain": ".morgancapitalgroup.com",
                "path": "/",
                "expiry": 9999999999,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ]
    )
    assert state["cookies"][0]["name"] == "mc_session"
    assert morgan_session.storage_state_has_session(state) is True
    assert morgan_session.storage_state_has_session({"cookies": []}) is False


def test_auth_result_options_patch_field():
    auth = AuthResult(
        success=True,
        status="authenticated",
        message="ok",
        options_patch={"storage_state": {"cookies": [{"name": "mc_session", "value": "x"}]}},
    )
    assert auth.options_patch is not None
    assert "storage_state" in auth.options_patch


def test_apply_options_patch_persists_storage_state():
    enc = get_encryption_service()
    aad_base = b"user:1:provider:morgan"
    conn = MagicMock()
    conn.user_id = 1
    conn.options_encrypted = enc.encrypt_dict({"pending_otp": "123456"}, aad=aad_base + b":options")
    provider = MagicMock()
    provider.provider_key = "morgan"
    db = MagicMock()
    db.get.return_value = provider

    with patch(
        "app.services.connection_service._aad_for_connection",
        return_value=aad_base,
    ):
        out = apply_options_patch(
            db,
            conn,
            {"storage_state": {"cookies": [{"name": "mc_session", "value": "tok"}]}},
            drop_keys=["pending_otp"],
        )
    assert "pending_otp" not in out
    assert out["storage_state"]["cookies"][0]["value"] == "tok"
    assert conn.options_encrypted is not None
    db.commit.assert_called()

    with patch(
        "app.services.connection_service._aad_for_connection",
        return_value=aad_base,
    ):
        assert connection_has_broker_session(db, conn) is True


@pytest.mark.asyncio
async def test_authenticate_connection_persists_options_patch():
    from app.services import broker_session as bs

    conn = MagicMock()
    conn.id = 9
    provider = MagicMock()
    provider.provider_key = "morgan"
    provider.id = 3
    conn.provider_id = 3

    connector = MagicMock()
    connector.authenticate = AsyncMock(
        return_value=AuthResult(
            success=True,
            status="authenticated",
            message="via state",
            options_patch={"storage_state": {"cookies": [{"name": "mc_session", "value": "z"}]}},
        )
    )
    connector.disconnect = AsyncMock()

    db = MagicMock()
    db.get.return_value = provider
    user = MagicMock()
    user.id = 1

    with (
        patch.object(bs, "get_connection", return_value=conn),
        patch.object(bs, "create_connector", return_value=connector),
        patch.object(bs, "decrypt_credentials", return_value={"username": "a", "password": "b"}),
        patch.object(bs, "decrypt_options", return_value={}),
        patch.object(bs, "apply_options_patch") as apply_patch,
        patch.object(bs, "get_provider_metadata") as meta,
    ):
        meta.return_value = MagicMock(capabilities=set())
        result = await bs.authenticate_connection(db, user, 9)
        apply_patch.assert_called_once()
        assert result[0] is conn
        assert result[4].success is True
