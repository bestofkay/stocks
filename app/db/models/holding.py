"""Holdings, snapshots, and transactions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class Holding(Base):
    """Latest known holding for each account + security identifier."""

    __tablename__ = "holdings"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "security_identifier",
            name="uq_holding_account_security",
        ),
        Index("ix_holdings_symbol", "symbol"),
        Index("ix_holdings_isin", "isin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    security_identifier: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    security_name: Mapped[str] = mapped_column(String(256), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    asset_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    average_cost: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    cost_basis: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    market_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    valuation_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_sync_id: Mapped[int | None] = mapped_column(
        ForeignKey("sync_runs.id", ondelete="SET NULL"), nullable=True
    )
    # observed | stale | confirmed_absent — reconciliation metadata
    observation_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="observed", server_default="observed"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    account = relationship("BrokerAccount", back_populates="holdings")


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    __table_args__ = (
        Index("ix_snapshots_user_captured", "user_id", "captured_at"),
        Index("ix_snapshots_connection_captured", "connection_id", "captured_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("broker_connections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("broker_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sync_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("sync_runs.id", ondelete="SET NULL"), nullable=True
    )
    total_market_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    total_cost_basis: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    cash_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    currency: Mapped[str] = mapped_column(String(16), nullable=False)
    valuation_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # broker_supplied | calculated
    valuation_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="calculated", server_default="calculated"
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    account = relationship("BrokerAccount", back_populates="snapshots")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "external_transaction_id",
            name="uq_transaction_external",
        ),
        Index("ix_transactions_date", "transaction_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_transaction_id: Mapped[str] = mapped_column(String(128), nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    fees: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    transaction_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    account = relationship("BrokerAccount", back_populates="transactions")
