"""Persisted synchronization schedules."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class SyncSchedule(Base):
    __tablename__ = "sync_schedules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("broker_connections.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="Null means all connections for the user",
    )
    # manual | hourly | every_6_hours | daily | weekly | cron
    schedule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    cron_expression: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user = relationship("User", back_populates="schedules")
    connection = relationship("BrokerConnection", back_populates="schedules")
