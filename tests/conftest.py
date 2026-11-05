"""Shared pytest fixtures."""

from __future__ import annotations

import base64
import os
import secrets

import pytest

# Ensure settings can load before importing app modules that call get_settings()
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
os.environ.setdefault("LOG_LEVEL", "WARNING")


@pytest.fixture
def encryption_key_b64() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")
