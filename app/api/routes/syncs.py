"""Synchronization status endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.syncs import SyncRunResponse
from app.services import sync_service

router = APIRouter(prefix="/syncs", tags=["syncs"])


@router.get("", response_model=list[SyncRunResponse])
def list_syncs(
    db: DbSession,
    user: CurrentUser,
    connection_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[SyncRunResponse]:
    runs = sync_service.list_syncs(db, user, connection_id=connection_id, limit=limit)
    return [SyncRunResponse.model_validate(r) for r in runs]


@router.get("/{sync_id}", response_model=SyncRunResponse)
def get_sync(sync_id: int, db: DbSession, user: CurrentUser) -> SyncRunResponse:
    run = sync_service.get_sync_run(db, user, sync_id)
    return SyncRunResponse.model_validate(run)


@router.post("/{sync_id}/cancel", response_model=SyncRunResponse)
def cancel_sync(sync_id: int, db: DbSession, user: CurrentUser) -> SyncRunResponse:
    run = sync_service.cancel_sync(db, user, sync_id)
    return SyncRunResponse.model_validate(run)
