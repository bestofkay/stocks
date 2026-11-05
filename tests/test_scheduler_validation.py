"""Scheduler cron / schedule type validation tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError
from app.services.scheduler_service import SCHEDULE_INTERVALS, create_schedule


def test_known_interval_types():
    assert "hourly" in SCHEDULE_INTERVALS
    assert "every_6_hours" in SCHEDULE_INTERVALS
    assert "daily" in SCHEDULE_INTERVALS
    assert "weekly" in SCHEDULE_INTERVALS


def test_invalid_cron_rejected():
    db = MagicMock()
    user = MagicMock(id=1)
    with pytest.raises(AppError) as exc:
        create_schedule(
            db,
            user,
            connection_id=None,
            schedule_type="cron",
            cron_expression="not a cron",
        )
    assert exc.value.status_code == 422


def test_unsupported_schedule_type():
    db = MagicMock()
    user = MagicMock(id=1)
    with pytest.raises(AppError):
        create_schedule(
            db,
            user,
            connection_id=None,
            schedule_type="biweekly",  # type: ignore[arg-type]
        )
