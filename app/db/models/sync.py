"""Synchronization run tracking."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("broker_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="queued", index=True)
    current_step: Mapped[str | None] = mapped_column(String(128), nullable=True)
    progress: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0"), server_default="0"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    records_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    accounts_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    holdings_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    safe_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    connection = relationship("BrokerConnection", back_populates="sync_runs")
    errors = relationship("SyncError", back_populates="sync_run", cascade="all, delete-orphan")


class SyncError(Base):
    __tablename__ = "sync_errors"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sync_run_id: Mapped[int] = mapped_column(
        ForeignKey("sync_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    error_code: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    sync_run = relationship("SyncRun", back_populates="errors")
