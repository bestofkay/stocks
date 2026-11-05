"""Synchronization schedule endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.schedules import ScheduleCreate, ScheduleResponse, ScheduleUpdate
from app.services import scheduler_service

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("", response_model=list[ScheduleResponse])
def list_schedules(db: DbSession, user: CurrentUser) -> list[ScheduleResponse]:
    return [ScheduleResponse.model_validate(s) for s in scheduler_service.list_schedules(db, user)]


@router.get("/{schedule_id}", response_model=ScheduleResponse)
def get_schedule(schedule_id: int, db: DbSession, user: CurrentUser) -> ScheduleResponse:
    sched = scheduler_service.get_schedule(db, user, schedule_id)
    return ScheduleResponse.model_validate(sched)


@router.post("", response_model=ScheduleResponse, status_code=201)
def create_schedule(
    body: ScheduleCreate,
    db: DbSession,
    user: CurrentUser,
) -> ScheduleResponse:
    sched = scheduler_service.create_schedule(
        db,
        user,
        connection_id=body.connection_id,
        schedule_type=body.schedule_type,
        cron_expression=body.cron_expression,
        enabled=body.enabled,
        notes=body.notes,
    )
    return ScheduleResponse.model_validate(sched)


@router.patch("/{schedule_id}", response_model=ScheduleResponse)
def update_schedule(
    schedule_id: int,
    body: ScheduleUpdate,
    db: DbSession,
    user: CurrentUser,
) -> ScheduleResponse:
    sched = scheduler_service.update_schedule(
        db,
        user,
        schedule_id,
        body.model_dump(exclude_unset=True),
    )
    return ScheduleResponse.model_validate(sched)


@router.delete("/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: int, db: DbSession, user: CurrentUser) -> None:
    scheduler_service.delete_schedule(db, user, schedule_id)
