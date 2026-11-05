"""Broker connections and discovered accounts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class BrokerConnection(Base):
    __tablename__ = "broker_connections"
    __table_args__ = (
        Index("ix_broker_connections_user_provider", "user_id", "provider_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Encrypted JSON envelope of credentials (never plaintext)
    credentials_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    options_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Separately encrypted session/cookie blob when persistence is required
    session_state_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="configured", index=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user = relationship("User", back_populates="connections")
    provider = relationship("Provider", back_populates="connections")
    accounts = relationship("BrokerAccount", back_populates="connection", cascade="all, delete-orphan")
    sync_runs = relationship("SyncRun", back_populates="connection", cascade="all, delete-orphan")
    schedules = relationship("SyncSchedule", back_populates="connection", cascade="all, delete-orphan")


class BrokerAccount(Base):
    __tablename__ = "broker_accounts"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_account_id", name="uq_account_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("broker_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    account_name: Mapped[str] = mapped_column(String(256), nullable=False)
    account_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    cash_balance: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    connection = relationship("BrokerConnection", back_populates="accounts")
    holdings = relationship("Holding", back_populates="account", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="account", cascade="all, delete-orphan")
    snapshots = relationship("PortfolioSnapshot", back_populates="account")
