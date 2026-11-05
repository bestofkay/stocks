"""APScheduler-based sync scheduling with MySQL advisory lock."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode, not_found
from app.core.security import utcnow
from app.db.database import get_engine, get_session_factory
from app.db.models import BrokerConnection, SyncSchedule, User
from app.services import sync_service
from app.services.audit_service import write_audit
from app.services.connection_service import get_connection

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_lock_held = False


SCHEDULE_INTERVALS = {
    "hourly": timedelta(hours=1),
    "every_6_hours": timedelta(hours=6),
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
}


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler


def try_acquire_scheduler_lock() -> bool:
    """MySQL GET_LOCK — ensures only one scheduler instance across workers."""
    global _lock_held
    settings = get_settings()
    try:
        with get_engine().connect() as conn:
            result = conn.execute(
                text("SELECT GET_LOCK(:key, 0)"),
                {"key": settings.scheduler_lock_key},
            )
            acquired = bool(result.scalar())
            _lock_held = acquired
            if acquired:
                logger.info("Acquired scheduler lock '%s'", settings.scheduler_lock_key)
            else:
                logger.warning(
                    "Scheduler lock '%s' held by another process — scheduler disabled here",
                    settings.scheduler_lock_key,
                )
            return acquired
    except Exception:
        logger.exception("Failed to acquire scheduler lock")
        return False


def release_scheduler_lock() -> None:
    global _lock_held
    if not _lock_held:
        return
    settings = get_settings()
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT RELEASE_LOCK(:key)"), {"key": settings.scheduler_lock_key})
        _lock_held = False
        logger.info("Released scheduler lock")
    except Exception:
        logger.exception("Failed to release scheduler lock")


async def start_scheduler() -> None:
    global _scheduler
    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("Scheduler disabled by configuration")
        return
    if not try_acquire_scheduler_lock():
        return

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        _tick_schedules,
        trigger=IntervalTrigger(minutes=1),
        id="schedule_ticker",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info("APScheduler started (tick every 1 minute)")
    # Load cron jobs from DB
    await _reload_cron_jobs()


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("APScheduler stopped")
    release_scheduler_lock()


async def _reload_cron_jobs() -> None:
    if _scheduler is None:
        return
    factory = get_session_factory()
    db = factory()
    try:
        schedules = (
            db.query(SyncSchedule)
            .filter(SyncSchedule.enabled.is_(True), SyncSchedule.schedule_type == "cron")
            .all()
        )
        for sched in schedules:
            job_id = f"cron-schedule-{sched.id}"
            if not sched.cron_expression:
                continue
            try:
                trigger = CronTrigger.from_crontab(sched.cron_expression, timezone="UTC")
                _scheduler.add_job(
                    _run_schedule,
                    trigger=trigger,
                    id=job_id,
                    replace_existing=True,
                    args=[sched.id],
                    max_instances=1,
                    coalesce=True,
                )
            except ValueError:
                logger.warning("Invalid cron expression for schedule %s: %s", sched.id, sched.cron_expression)
    finally:
        db.close()


async def _tick_schedules() -> None:
    """Check interval-based schedules due for execution."""
    factory = get_session_factory()
    db = factory()
    try:
        now = utcnow()
        schedules = (
            db.query(SyncSchedule)
            .filter(
                SyncSchedule.enabled.is_(True),
                SyncSchedule.schedule_type.in_(list(SCHEDULE_INTERVALS.keys())),
            )
            .all()
        )
        for sched in schedules:
            interval = SCHEDULE_INTERVALS.get(sched.schedule_type)
            if interval is None:
                continue
            due = sched.next_run_at is None or sched.next_run_at <= now
            if not due:
                continue
            await _execute_schedule(db, sched)
    except Exception:
        logger.exception("Schedule tick failed")
    finally:
        db.close()


async def _run_schedule(schedule_id: int) -> None:
    factory = get_session_factory()
    db = factory()
    try:
        sched = db.get(SyncSchedule, schedule_id)
        if sched is None or not sched.enabled:
            return
        await _execute_schedule(db, sched)
    finally:
        db.close()


async def _execute_schedule(db: Session, sched: SyncSchedule) -> None:
    now = utcnow()
    try:
        user = db.get(User, sched.user_id)
        if user is None:
            return
        connection_ids: list[int]
        if sched.connection_id:
            connection_ids = [sched.connection_id]
        else:
            connection_ids = [
                c.id
                for c in db.query(BrokerConnection)
                .filter(BrokerConnection.user_id == user.id)
                .all()
            ]
        for cid in connection_ids:
            try:
                run = sync_service.enqueue_sync(db, user, cid)
                await sync_service.start_sync_background(run.id)
            except AppError as exc:
                if exc.code == ErrorCode.SYNC_ALREADY_RUNNING:
                    logger.info("Skipping scheduled sync for connection %s: already running", cid)
                else:
                    logger.warning("Scheduled sync failed for connection %s: %s", cid, exc.message)

        sched.last_run_at = now
        if sched.schedule_type in SCHEDULE_INTERVALS:
            sched.next_run_at = now + SCHEDULE_INTERVALS[sched.schedule_type]
        db.commit()
    except Exception:
        logger.exception("Failed executing schedule %s", sched.id)
        db.rollback()


def create_schedule(
    db: Session,
    user: User,
    *,
    connection_id: int | None,
    schedule_type: str,
    cron_expression: str | None = None,
    enabled: bool = True,
    notes: str | None = None,
) -> SyncSchedule:
    if connection_id is not None:
        get_connection(db, user, connection_id)

    if schedule_type == "cron":
        try:
            CronTrigger.from_crontab(cron_expression or "", timezone="UTC")
        except ValueError as exc:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                f"Invalid cron expression: {exc}",
                status_code=422,
            ) from exc
    elif schedule_type not in {"manual", *SCHEDULE_INTERVALS.keys()}:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"Unsupported schedule_type: {schedule_type}",
            status_code=422,
        )

    next_run = None
    if schedule_type in SCHEDULE_INTERVALS:
        next_run = utcnow() + SCHEDULE_INTERVALS[schedule_type]

    sched = SyncSchedule(
        user_id=user.id,
        connection_id=connection_id,
        schedule_type=schedule_type,
        cron_expression=cron_expression,
        enabled=enabled,
        notes=notes,
        next_run_at=next_run,
    )
    db.add(sched)
    db.flush()
    write_audit(
        db,
        user_id=user.id,
        action="schedule.create",
        resource_type="sync_schedule",
        resource_id=sched.id,
        metadata={"schedule_type": schedule_type},
    )
    db.commit()
    db.refresh(sched)

    # Hot-reload cron job if scheduler is running
    if _scheduler and schedule_type == "cron" and enabled and cron_expression:
        trigger = CronTrigger.from_crontab(cron_expression, timezone="UTC")
        _scheduler.add_job(
            _run_schedule,
            trigger=trigger,
            id=f"cron-schedule-{sched.id}",
            replace_existing=True,
            args=[sched.id],
            max_instances=1,
        )
    return sched


def list_schedules(db: Session, user: User) -> list[SyncSchedule]:
    return (
        db.query(SyncSchedule)
        .filter(SyncSchedule.user_id == user.id)
        .order_by(SyncSchedule.id.desc())
        .all()
    )


def get_schedule(db: Session, user: User, schedule_id: int) -> SyncSchedule:
    sched = db.get(SyncSchedule, schedule_id)
    if sched is None or sched.user_id != user.id:
        raise not_found(ErrorCode.SCHEDULE_NOT_FOUND, "Schedule not found")
    return sched


def update_schedule(
    db: Session,
    user: User,
    schedule_id: int,
    updates: dict[str, Any],
) -> SyncSchedule:
    sched = get_schedule(db, user, schedule_id)
    if "connection_id" in updates and updates["connection_id"] is not None:
        get_connection(db, user, updates["connection_id"])
        sched.connection_id = updates["connection_id"]
    if "schedule_type" in updates and updates["schedule_type"] is not None:
        sched.schedule_type = updates["schedule_type"]
    if "cron_expression" in updates:
        sched.cron_expression = updates["cron_expression"]
    if "enabled" in updates and updates["enabled"] is not None:
        sched.enabled = updates["enabled"]
    if "notes" in updates:
        sched.notes = updates["notes"]

    if sched.schedule_type == "cron" and sched.cron_expression:
        try:
            CronTrigger.from_crontab(sched.cron_expression, timezone="UTC")
        except ValueError as exc:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                f"Invalid cron expression: {exc}",
                status_code=422,
            ) from exc

    if sched.schedule_type in SCHEDULE_INTERVALS and sched.next_run_at is None:
        sched.next_run_at = utcnow() + SCHEDULE_INTERVALS[sched.schedule_type]

    write_audit(
        db,
        user_id=user.id,
        action="schedule.update",
        resource_type="sync_schedule",
        resource_id=sched.id,
    )
    db.commit()
    db.refresh(sched)
    return sched


def delete_schedule(db: Session, user: User, schedule_id: int) -> None:
    sched = get_schedule(db, user, schedule_id)
    write_audit(
        db,
        user_id=user.id,
        action="schedule.delete",
        resource_type="sync_schedule",
        resource_id=sched.id,
    )
    if _scheduler:
        job_id = f"cron-schedule-{sched.id}"
        if _scheduler.get_job(job_id):
            _scheduler.remove_job(job_id)
    db.delete(sched)
    db.commit()
