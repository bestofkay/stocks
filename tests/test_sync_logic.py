"""Sync orchestration unit tests (no live brokers / no MySQL required)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.connectors.models import NormalizedHolding
from app.core.errors import AppError, ErrorCode
from app.services import sync_service


def test_upsert_holding_creates_then_updates():
    db = MagicMock()
    account = SimpleNamespace(id=1)
    db.query.return_value.filter.return_value.first.return_value = None

    holding = NormalizedHolding(
        symbol="MTNN",
        security_name="MTN Nigeria",
        quantity=Decimal("1000"),
        market_value=Decimal("220000"),
        currency="NGN",
    )
    result = sync_service._upsert_holding(db, account, holding, sync_id=9)
    assert db.add.called
    assert result.security_identifier == "symbol:MTNN:NGN"
    assert result.last_seen_sync_id == 9
    assert result.observation_status == "observed"

    existing = MagicMock()
    existing.security_identifier = "symbol:MTNN:NGN"
    db.query.return_value.filter.return_value.first.return_value = existing
    holding2 = holding.model_copy(update={"quantity": Decimal("1500")})
    sync_service._upsert_holding(db, account, holding2, sync_id=10)
    assert existing.quantity == Decimal("1500")
    assert existing.last_seen_sync_id == 10


def test_mark_holdings_stale():
    db = MagicMock()
    account = SimpleNamespace(id=5)
    sync_service._mark_holdings_stale(db, account)
    db.query.return_value.filter.return_value.update.assert_called_once()


def test_recover_interrupted_syncs():
    db = MagicMock()
    run = MagicMock()
    run.connection_id = 3
    run.status = "running"
    conn = MagicMock()
    conn.status = "syncing"
    db.query.return_value.filter.return_value.all.return_value = [run]
    db.get.return_value = conn

    count = sync_service.recover_interrupted_syncs(db)
    assert count == 1
    assert run.status == "failed"
    assert "Interrupted" in run.safe_error_message
    assert conn.status == "configured"
    db.commit.assert_called()


def test_cancel_sync_rejects_completed(monkeypatch):
    db = MagicMock()
    user = MagicMock(id=1)
    run = MagicMock()
    run.status = "completed"
    run.id = 1
    monkeypatch.setattr(sync_service, "get_sync_run", lambda *_a, **_k: run)
    with pytest.raises(AppError) as exc:
        sync_service.cancel_sync(db, user, 1)
    assert exc.value.code == ErrorCode.CONFLICT


def test_cancel_sync_success(monkeypatch):
    db = MagicMock()
    user = MagicMock(id=1)
    run = MagicMock()
    run.status = "running"
    run.id = 7
    run.connection_id = 3
    conn = MagicMock()
    conn.status = "syncing"
    db.get.return_value = conn
    monkeypatch.setattr(sync_service, "get_sync_run", lambda *_a, **_k: run)
    sync_service._running_tasks.clear()

    result = sync_service.cancel_sync(db, user, 7)
    assert result.status == "cancelled"
    assert conn.status == "configured"
    db.commit.assert_called()


@pytest.mark.asyncio
async def test_shutdown_sync_tasks_empty():
    sync_service._running_tasks.clear()
    await sync_service.shutdown_sync_tasks(timeout_seconds=0.1)
