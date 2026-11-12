"""Tests for shortened connect → sync → ready client flow."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.schemas.connections import ConnectionCreate, ConnectionCreateResponse, ConnectionReadyResponse
from app.schemas.syncs import SyncAcceptedResponse, SyncSummary
from app.services import sync_service


def test_connection_create_schema_defaults_sync_true():
    body = ConnectionCreate(
        provider_id="fundvine",
        display_name="Main",
        credentials={"username": "u", "password": "p"},
    )
    assert body.sync is True


def test_connection_create_response_includes_sync_and_ready_url():
    now = datetime.now(timezone.utc)
    resp = ConnectionCreateResponse.model_validate(
        {
            "id": 7,
            "provider_id": "fundvine",
            "provider_key": "fundvine",
            "display_name": "Main",
            "status": "syncing",
            "last_sync_at": None,
            "last_successful_sync_at": None,
            "last_error_code": None,
            "has_credentials": True,
            "has_broker_session": False,
            "latest_sync": {
                "sync_id": 99,
                "status": "queued",
                "poll_url": "/api/v1/syncs/99",
                "current_step": "queued",
                "progress": "0",
                "safe_error_message": None,
            },
            "portfolio_ready": False,
            "created_at": now,
            "updated_at": now,
            "sync": {
                "sync_id": 99,
                "status": "queued",
                "message": "Provider sync started",
                "poll_url": "/api/v1/syncs/99",
                "connection_id": 7,
            },
            "ready_url": "/api/v1/connections/7/ready",
        }
    )
    assert resp.sync is not None
    assert resp.sync.sync_id == 99
    assert resp.ready_url == "/api/v1/connections/7/ready"
    assert resp.latest_sync is not None
    assert resp.latest_sync.poll_url.endswith("/99")


def test_sync_accepted_helper():
    run = SimpleNamespace(id=55, status="queued", connection_id=12)
    payload = sync_service.sync_accepted(run)
    assert payload == {
        "sync_id": 55,
        "status": "queued",
        "message": "Synchronization accepted",
        "poll_url": "/api/v1/syncs/55",
        "connection_id": 12,
    }
    assert SyncAcceptedResponse.model_validate(payload).poll_url == "/api/v1/syncs/55"


def test_connection_ready_payload_completed():
    user = SimpleNamespace(id=1)
    db = MagicMock()
    conn = SimpleNamespace(
        id=12,
        status="configured",
        last_successful_sync_at=datetime.now(timezone.utc),
    )
    run = SimpleNamespace(
        id=55,
        status="completed",
        current_step="done",
        progress=Decimal("100"),
        safe_error_message=None,
        connection_id=12,
    )

    original_get = sync_service.get_connection
    original_latest = sync_service.latest_sync_for_connection
    sync_service.get_connection = lambda _db, _user, _cid: conn  # type: ignore
    sync_service.latest_sync_for_connection = lambda _db, _cid: run  # type: ignore
    try:
        payload = sync_service.connection_ready_payload(db, user, 12)
        ready = ConnectionReadyResponse.model_validate(payload)
        assert ready.ready is True
        assert ready.latest_sync is not None
        assert ready.latest_sync.sync_id == 55
        assert "ready" in ready.message.lower()
    finally:
        sync_service.get_connection = original_get  # type: ignore
        sync_service.latest_sync_for_connection = original_latest  # type: ignore


def test_connection_ready_payload_in_progress():
    user = SimpleNamespace(id=1)
    db = MagicMock()
    conn = SimpleNamespace(id=12, status="syncing", last_successful_sync_at=None)
    run = SimpleNamespace(
        id=56,
        status="running",
        current_step="holdings",
        progress=Decimal("40"),
        safe_error_message=None,
        connection_id=12,
    )
    original_get = sync_service.get_connection
    original_latest = sync_service.latest_sync_for_connection
    sync_service.get_connection = lambda _db, _user, _cid: conn  # type: ignore
    sync_service.latest_sync_for_connection = lambda _db, _cid: run  # type: ignore
    try:
        ready = ConnectionReadyResponse.model_validate(
            sync_service.connection_ready_payload(db, user, 12)
        )
        assert ready.ready is False
        assert ready.latest_sync is not None
        assert SyncSummary.model_validate(ready.latest_sync.model_dump()).status == "running"
    finally:
        sync_service.get_connection = original_get  # type: ignore
        sync_service.latest_sync_for_connection = original_latest  # type: ignore


def test_connection_has_broker_session_cookies():
    from app.services import connection_service

    db = MagicMock()
    conn = SimpleNamespace(id=1, user_id=1, provider_id=1, options_encrypted="x")
    original = connection_service.decrypt_options
    connection_service.decrypt_options = lambda _db, _conn: {  # type: ignore
        "api_session": {"cookies": {"__NAYA.AUTH": "abc"}, "client_id": 1}
    }
    try:
        assert connection_service.connection_has_broker_session(db, conn) is True
    finally:
        connection_service.decrypt_options = original  # type: ignore
