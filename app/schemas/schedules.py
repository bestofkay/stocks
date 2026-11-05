"""Schedule schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ScheduleType = Literal["manual", "hourly", "every_6_hours", "daily", "weekly", "cron"]


class ScheduleCreate(BaseModel):
    connection_id: int | None = None
    schedule_type: ScheduleType
    cron_expression: str | None = Field(default=None, max_length=128)
    enabled: bool = True
    notes: str | None = None

    @model_validator(mode="after")
    def validate_cron(self) -> ScheduleCreate:
        if self.schedule_type == "cron" and not self.cron_expression:
            raise ValueError("cron_expression is required when schedule_type is cron")
        if self.schedule_type != "cron" and self.cron_expression:
            raise ValueError("cron_expression is only allowed when schedule_type is cron")
        return self


class ScheduleUpdate(BaseModel):
    schedule_type: ScheduleType | None = None
    cron_expression: str | None = None
    enabled: bool | None = None
    notes: str | None = None
    connection_id: int | None = None


class ScheduleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    connection_id: int | None
    schedule_type: str
    cron_expression: str | None
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
