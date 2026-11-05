"""Broker connection management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from app.api.deps import CurrentUser, DbSession
from app.schemas.connections import (
    ConnectionCreate,
    ConnectionResponse,
    ConnectionTestResponse,
    ConnectionUpdate,
    MfaSubmitRequest,
)
from app.schemas.syncs import SyncAcceptedResponse, SyncRunResponse
from app.services import connection_service, sync_service
from app.services.connection_service import connection_to_response

router = APIRouter(prefix="/connections", tags=["connections"])


@router.post("", response_model=ConnectionResponse, status_code=201)
def create_connection(
    body: ConnectionCreate,
    db: DbSession,
    user: CurrentUser,
    background_tasks: BackgroundTasks,
) -> ConnectionResponse:
    conn = connection_service.create_connection(
        db,
        user,
        provider_id=body.provider_id,
        display_name=body.display_name,
        credentials=body.credentials,
        options=body.options,
    )
    # First sync upserts holdings/cash into DB for loan collateral
    run = sync_service.try_enqueue_sync(db, user, conn.id)
    if run is not None:
        background_tasks.add_task(sync_service.start_sync_background, run.id)
    return ConnectionResponse.model_validate(connection_to_response(db, conn))


@router.get("", response_model=list[ConnectionResponse])
def list_connections(db: DbSession, user: CurrentUser) -> list[ConnectionResponse]:
    conns = connection_service.list_connections(db, user)
    return [ConnectionResponse.model_validate(connection_to_response(db, c)) for c in conns]


@router.get("/{connection_id}", response_model=ConnectionResponse)
def get_connection(connection_id: int, db: DbSession, user: CurrentUser) -> ConnectionResponse:
    conn = connection_service.get_connection(db, user, connection_id)
    return ConnectionResponse.model_validate(connection_to_response(db, conn))


@router.patch("/{connection_id}", response_model=ConnectionResponse)
def update_connection(
    connection_id: int,
    body: ConnectionUpdate,
    db: DbSession,
    user: CurrentUser,
) -> ConnectionResponse:
    conn = connection_service.update_connection(
        db,
        user,
        connection_id,
        display_name=body.display_name,
        credentials=body.credentials,
        options=body.options,
        status=body.status,
    )
    return ConnectionResponse.model_validate(connection_to_response(db, conn))


@router.delete("/{connection_id}", status_code=204)
def delete_connection(connection_id: int, db: DbSession, user: CurrentUser) -> None:
    connection_service.delete_connection(db, user, connection_id)


@router.post("/{connection_id}/test", response_model=ConnectionTestResponse)
async def test_connection(
    connection_id: int,
    db: DbSession,
    user: CurrentUser,
) -> ConnectionTestResponse:
    result = await connection_service.test_connection(db, user, connection_id)
    return ConnectionTestResponse.model_validate(result)


@router.post("/{connection_id}/mfa", response_model=ConnectionTestResponse)
async def submit_connection_mfa(
    connection_id: int,
    body: MfaSubmitRequest,
    db: DbSession,
    user: CurrentUser,
) -> ConnectionTestResponse:
    """Submit OTP/MFA for a connection currently in mfa_required / waiting_for_user."""
    result = await connection_service.submit_mfa(db, user, connection_id, body.code)
    return ConnectionTestResponse.model_validate(result)


@router.post(
    "/{connection_id}/sync",
    response_model=SyncAcceptedResponse,
    status_code=202,
)
async def sync_connection(
    connection_id: int,
    db: DbSession,
    user: CurrentUser,
    background_tasks: BackgroundTasks,
) -> SyncAcceptedResponse:
    run = sync_service.enqueue_sync(db, user, connection_id)
    background_tasks.add_task(sync_service.start_sync_background, run.id)
    return SyncAcceptedResponse(
        sync_id=run.id,
        status=run.status,
        poll_url=f"/api/v1/syncs/{run.id}",
    )


@router.get("/{connection_id}/syncs", response_model=list[SyncRunResponse])
def list_connection_syncs(
    connection_id: int,
    db: DbSession,
    user: CurrentUser,
) -> list[SyncRunResponse]:
    connection_service.get_connection(db, user, connection_id)
    runs = sync_service.list_syncs(db, user, connection_id=connection_id)
    return [SyncRunResponse.model_validate(r) for r in runs]
