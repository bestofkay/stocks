"""Tests for deposit instructions + order APIs (no live broker money movement)."""

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
from app.connectors.afrinvestor.parser import (  # noqa: E402
    deposit_instructions_from_dedicated_api,
    find_equity_in_search,
    order_result_from_broker_response,
)
from app.connectors.base import BrokerConnector  # noqa: E402
from app.connectors.example_broker.connector import ExampleBrokerConnector  # noqa: E402
from app.connectors.models import (  # noqa: E402
    DataCapability,
    DepositInstructions,
    OrderRequest,
    OrderResult,
    OrderSide,
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


def test_deposit_parser_from_list_payload():
    instr = deposit_instructions_from_dedicated_api(
        [
            {
                "bank_code": "50515",
                "bank_name": "MONIEPOINT",
                "account_name": "SOJ",
                "account_number": "1036495647",
            }
        ]
    )
    assert instr.bank_name == "MONIEPOINT"
    assert instr.account_number == "1036495647"
    assert instr.account_name == "SOJ"
    assert instr.bank_code == "50515"
    assert instr.currency == "NGN"


def test_find_equity_and_order_result_parsers():
    eq = find_equity_in_search(
        {"data": [{"secId": "ACCESSCORP", "symbol": "ACCESSCORP", "price": 28.5}]},
        "accesscorp",
    )
    assert eq is not None
    assert eq["secId"] == "ACCESSCORP"
    result = order_result_from_broker_response(
        {"order_id": "ord-1", "status": "NEW", "message": "submitted"},
        side="buy",
        symbol="ACCESSCORP",
    )
    assert result.external_order_id == "ord-1"
    assert result.side == OrderSide.BUY
    assert result.status == "NEW"


def test_afrinvestor_advertises_write_capabilities():
    meta = get_provider_metadata("afrinvestor")
    assert DataCapability.DEPOSIT_INSTRUCTIONS in meta.capabilities
    assert DataCapability.ORDERS in meta.capabilities


@pytest.mark.asyncio
async def test_example_broker_write_methods_not_implemented():
    c = ExampleBrokerConnector()
    with pytest.raises(NotImplementedError):
        await c.get_deposit_instructions()
    with pytest.raises(NotImplementedError):
        await c.place_order(
            OrderRequest(side=OrderSide.BUY, symbol="X", quantity=Decimal("1"))
        )


@pytest.mark.asyncio
async def test_base_connector_write_defaults():
    class _Dummy(BrokerConnector):
        provider_id = "dummy"
        provider_name = "Dummy"

        @classmethod
        def metadata(cls):  # pragma: no cover - unused
            raise NotImplementedError

        async def authenticate(self, credentials, options=None):  # pragma: no cover
            raise NotImplementedError

        async def get_accounts(self):  # pragma: no cover
            return []

        async def get_holdings(self, account):  # pragma: no cover
            return []

        async def disconnect(self):
            return None

    dummy = _Dummy()
    with pytest.raises(NotImplementedError):
        await dummy.get_deposit_instructions()
    with pytest.raises(NotImplementedError):
        await dummy.place_order(
            OrderRequest(side=OrderSide.BUY, symbol="X", quantity=Decimal("1"))
        )


def test_deposits_instructions_route_exists(client):
    r = client.get("/api/v1/deposits/instructions")
    # missing connection_id → validation error (route is registered)
    assert r.status_code == 422


def test_orders_route_validation(client):
    r = client.post("/api/v1/orders", json={"connection_id": 1, "side": "buy", "symbol": "X"})
    assert r.status_code == 422  # quantity/amount required


@patch("app.services.broker_session.get_connection")
@patch("app.services.broker_session.get_provider_metadata")
def test_deposit_instructions_501_for_unsupported_provider(
    mock_meta, mock_get_conn, client, user
):
    mock_get_conn.return_value = SimpleNamespace(id=9, provider_id=1, user_id=user.id)
    mock_meta.return_value = SimpleNamespace(
        capabilities=[],  # no deposit_instructions
    )
    db = MagicMock()
    provider = SimpleNamespace(provider_key="example_broker")
    db.get.return_value = provider

    app = client.app

    def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    r = client.get("/api/v1/deposits/instructions?connection_id=9")
    assert r.status_code == 501
    assert r.json()["error"]["code"] == ErrorCode.CONNECTOR_NOT_IMPLEMENTED.value


@patch("app.services.broker_session.get_connection")
@patch("app.services.broker_session.get_provider_metadata")
def test_orders_501_for_unsupported_provider(mock_meta, mock_get_conn, client, user):
    mock_get_conn.return_value = SimpleNamespace(id=9, provider_id=1, user_id=user.id)
    mock_meta.return_value = SimpleNamespace(capabilities=[])
    db = MagicMock()
    db.get.return_value = SimpleNamespace(provider_key="example_broker")

    def _db():
        yield db

    client.app.dependency_overrides[get_db] = _db
    r = client.post(
        "/api/v1/orders",
        json={
            "connection_id": 9,
            "side": "buy",
            "symbol": "ACCESSCORP",
            "quantity": "1",
        },
    )
    assert r.status_code == 501
    assert r.json()["error"]["code"] == ErrorCode.CONNECTOR_NOT_IMPLEMENTED.value


@patch("app.services.deposit_service.write_audit")
@patch("app.services.broker_session.apply_options_patch")
@patch("app.services.broker_session.create_connector")
@patch("app.services.broker_session.decrypt_options", return_value={})
@patch(
    "app.services.broker_session.decrypt_credentials",
    return_value={"username": "u", "password": "p"},
)
@patch("app.services.broker_session.get_provider_metadata")
@patch("app.services.broker_session.get_connection")
def test_deposit_instructions_success(
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
    mock_meta.return_value = SimpleNamespace(
        capabilities=[DataCapability.DEPOSIT_INSTRUCTIONS]
    )
    db = MagicMock()
    db.get.return_value = SimpleNamespace(provider_key="afrinvestor")

    connector = AsyncMock()
    connector.authenticate.return_value = SimpleNamespace(
        success=True,
        status="authenticated",
        message="ok",
        options_patch=None,
    )
    connector.get_deposit_instructions.return_value = DepositInstructions(
        bank_name="MONIEPOINT",
        account_number="1036495647",
        account_name="SOJ",
        currency="NGN",
        bank_code="50515",
        provider_reference="50515",
        raw={"bank_name": "MONIEPOINT"},
    )
    connector.disconnect.return_value = None
    mock_create.return_value = connector

    def _db():
        yield db

    client.app.dependency_overrides[get_db] = _db
    r = client.get("/api/v1/deposits/instructions?connection_id=3")
    assert r.status_code == 200
    body = r.json()
    assert body["account_number"] == "1036495647"
    assert body["bank_name"] == "MONIEPOINT"
    assert body["provider_id"] == "afrinvestor"


@patch("app.services.order_service.write_audit")
@patch("app.services.broker_session.apply_options_patch")
@patch("app.services.broker_session.create_connector")
@patch("app.services.broker_session.decrypt_options", return_value={})
@patch(
    "app.services.broker_session.decrypt_credentials",
    return_value={"username": "u", "password": "p"},
)
@patch("app.services.broker_session.get_provider_metadata")
@patch("app.services.broker_session.get_connection")
def test_place_order_success(
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
    mock_meta.return_value = SimpleNamespace(capabilities=[DataCapability.ORDERS])
    db = MagicMock()
    db.get.return_value = SimpleNamespace(provider_key="afrinvestor")

    connector = AsyncMock()
    connector.authenticate.return_value = SimpleNamespace(
        success=True,
        status="authenticated",
        message="ok",
        options_patch=None,
    )
    connector.place_order.return_value = OrderResult(
        external_order_id="ord-99",
        status="NEW",
        side=OrderSide.BUY,
        symbol="ACCESSCORP",
        message="submitted",
        raw={"order_id": "ord-99"},
    )
    connector.disconnect.return_value = None
    mock_create.return_value = connector

    def _db():
        yield db

    client.app.dependency_overrides[get_db] = _db
    r = client.post(
        "/api/v1/orders",
        json={
            "connection_id": 3,
            "side": "sell",
            "symbol": "ACCESSCORP",
            "quantity": "1",
            "order_type": "market",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["external_order_id"] == "ord-99"
    assert body["status"] == "NEW"
    connector.place_order.assert_awaited()
