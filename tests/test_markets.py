"""Tests for market / instrument listing."""

from __future__ import annotations

import base64
import os
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
from app.connectors.afrinvestor.parser import instruments_from_equities_api  # noqa: E402
from app.connectors.models import (  # noqa: E402
    DataCapability,
    InstrumentListResult,
    MarketInfo,
    MarketInstrument,
)
from app.connectors.registry import get_provider_metadata  # noqa: E402
from app.core.config import clear_settings_cache  # noqa: E402
from app.core.errors import ErrorCode  # noqa: E402
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


def test_instruments_parser():
    result = instruments_from_equities_api(
        {
            "message": "ok",
            "data": [
                {
                    "id": "abc",
                    "symbol": "ACCESSCORP",
                    "secId": "ACCESSCORP",
                    "secDesc": "ACCESS HOLDINGS PLC",
                    "exchangeId": "NGX",
                    "marketId": "uuid",
                    "price": 28.5,
                    "currency": "NGN",
                    "marketSegmentId": "EQUITY",
                }
            ],
        },
        market_id="NGX",
        page=1,
        per_page=100,
    )
    assert result.total == 1
    assert result.instruments[0].symbol == "ACCESSCORP"
    assert result.instruments[0].last_price == Decimal("28.5")
    assert result.instruments[0].exchange_id == "NGX"


def test_afrinvestor_advertises_instruments_capability():
    meta = get_provider_metadata("afrinvestor")
    assert DataCapability.INSTRUMENTS in meta.capabilities


def test_markets_routes_require_connection_id(client):
    assert client.get("/api/v1/markets").status_code == 422
    assert client.get("/api/v1/markets/instruments").status_code == 422


@patch("app.services.broker_session.get_connection")
@patch("app.services.broker_session.get_provider_metadata")
def test_markets_501_unsupported(mock_meta, mock_get_conn, client, user):
    mock_get_conn.return_value = SimpleNamespace(id=9, provider_id=1, user_id=user.id)
    mock_meta.return_value = SimpleNamespace(capabilities=[])
    db = MagicMock()
    db.get.return_value = SimpleNamespace(provider_key="example_broker")

    def _db():
        yield db

    client.app.dependency_overrides[get_db] = _db
    r = client.get("/api/v1/markets?connection_id=9")
    assert r.status_code == 501
    assert r.json()["error"]["code"] == ErrorCode.CONNECTOR_NOT_IMPLEMENTED.value


@patch("app.services.market_service.write_audit")
@patch("app.services.broker_session.apply_options_patch")
@patch("app.services.broker_session.create_connector")
@patch("app.services.broker_session.decrypt_options", return_value={})
@patch(
    "app.services.broker_session.decrypt_credentials",
    return_value={"username": "u", "password": "p"},
)
@patch("app.services.broker_session.get_provider_metadata")
@patch("app.services.broker_session.get_connection")
def test_list_markets_and_instruments_success(
    mock_get_conn,
    mock_meta,
    _creds,
    _opts,
    mock_create,
    _patch,
    _audit,
    client,
    user,
):
    mock_get_conn.return_value = SimpleNamespace(id=3, provider_id=1, user_id=user.id)
    mock_meta.return_value = SimpleNamespace(capabilities=[DataCapability.INSTRUMENTS])
    db = MagicMock()
    db.get.return_value = SimpleNamespace(provider_key="afrinvestor")

    connector = AsyncMock()
    connector.authenticate.return_value = SimpleNamespace(
        success=True, status="authenticated", message="ok", options_patch=None
    )
    connector.list_markets.return_value = [
        MarketInfo(market_id="NGX", name="Nigerian Exchange (NGX)", currency="NGN")
    ]
    connector.list_instruments.return_value = InstrumentListResult(
        market_id="NGX",
        page=1,
        per_page=100,
        total=1,
        instruments=[
            MarketInstrument(
                symbol="ACCESSCORP",
                name="ACCESS HOLDINGS PLC",
                market_id="NGX",
                exchange_id="NGX",
                currency="NGN",
                last_price=Decimal("28.5"),
            )
        ],
    )
    connector.disconnect.return_value = None
    mock_create.return_value = connector

    def _db():
        yield db

    client.app.dependency_overrides[get_db] = _db

    r1 = client.get("/api/v1/markets?connection_id=3")
    assert r1.status_code == 200
    assert r1.json()["markets"][0]["market_id"] == "NGX"

    r2 = client.get("/api/v1/markets/instruments?connection_id=3&search=ACCESS")
    assert r2.status_code == 200
    body = r2.json()
    assert body["total"] == 1
    assert body["instruments"][0]["symbol"] == "ACCESSCORP"
