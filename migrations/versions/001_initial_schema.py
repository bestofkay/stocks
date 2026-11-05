"""Initial schema

Revision ID: 001_initial
Revises:
Create Date: 2026-10-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # 191 chars keeps utf8mb4 unique index under older MySQL/MariaDB 1000-byte key limit
        sa.Column("email", sa.String(length=191), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "providers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("connector_type", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("implementation_status", sa.String(length=32), server_default="registered", nullable=False),
        sa.Column("capabilities_json", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_key"),
    )
    op.create_index("ix_providers_provider_key", "providers", ["provider_key"])

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_session_id", "user_sessions", ["session_id"])
    op.create_index("ix_user_sessions_refresh_token_hash", "user_sessions", ["refresh_token_hash"])

    op.create_table(
        "broker_connections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("credentials_encrypted", sa.Text(), nullable=False),
        sa.Column("options_encrypted", sa.Text(), nullable=True),
        sa.Column("session_state_encrypted", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_broker_connections_user_id", "broker_connections", ["user_id"])
    op.create_index("ix_broker_connections_provider_id", "broker_connections", ["provider_id"])
    op.create_index("ix_broker_connections_status", "broker_connections", ["status"])
    op.create_index("ix_broker_connections_user_provider", "broker_connections", ["user_id", "provider_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("safe_metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    op.create_table(
        "broker_accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("external_account_id", sa.String(length=128), nullable=False),
        sa.Column("account_name", sa.String(length=256), nullable=False),
        sa.Column("account_type", sa.String(length=64), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("cash_balance", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["broker_connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", "external_account_id", name="uq_account_external"),
    )
    op.create_index("ix_broker_accounts_connection_id", "broker_accounts", ["connection_id"])

    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("current_step", sa.String(length=128), nullable=True),
        sa.Column("progress", sa.Numeric(precision=5, scale=2), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("records_found", sa.Integer(), server_default="0", nullable=False),
        sa.Column("accounts_found", sa.Integer(), server_default="0", nullable=False),
        sa.Column("holdings_found", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("safe_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["broker_connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_runs_connection_id", "sync_runs", ["connection_id"])
    op.create_index("ix_sync_runs_status", "sync_runs", ["status"])

    op.create_table(
        "sync_schedules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("schedule_type", sa.String(length=32), nullable=False),
        sa.Column("cron_expression", sa.String(length=128), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["broker_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_schedules_user_id", "sync_schedules", ["user_id"])
    op.create_index("ix_sync_schedules_connection_id", "sync_schedules", ["connection_id"])

    op.create_table(
        "sync_errors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sync_run_id", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=False),
        sa.Column("safe_message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["sync_run_id"], ["sync_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_errors_sync_run_id", "sync_errors", ["sync_run_id"])

    op.create_table(
        "holdings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("security_identifier", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=True),
        sa.Column("security_name", sa.String(length=256), nullable=False),
        sa.Column("isin", sa.String(length=32), nullable=True),
        sa.Column("asset_type", sa.String(length=64), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("average_cost", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("cost_basis", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("current_price", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("market_value", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("unrealized_pnl", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("valuation_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_sync_id", sa.Integer(), nullable=True),
        sa.Column("observation_status", sa.String(length=32), server_default="observed", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["broker_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_seen_sync_id"], ["sync_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "security_identifier", name="uq_holding_account_security"),
    )
    op.create_index("ix_holdings_account_id", "holdings", ["account_id"])
    op.create_index("ix_holdings_symbol", "holdings", ["symbol"])
    op.create_index("ix_holdings_isin", "holdings", ["isin"])

    op.create_table(
        "portfolio_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("sync_run_id", sa.Integer(), nullable=True),
        sa.Column("total_market_value", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("total_cost_basis", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("cash_value", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("unrealized_pnl", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("valuation_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valuation_source", sa.String(length=32), server_default="calculated", nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["broker_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["connection_id"], ["broker_connections.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["sync_run_id"], ["sync_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_portfolio_snapshots_user_id", "portfolio_snapshots", ["user_id"])
    op.create_index("ix_portfolio_snapshots_connection_id", "portfolio_snapshots", ["connection_id"])
    op.create_index("ix_portfolio_snapshots_account_id", "portfolio_snapshots", ["account_id"])
    op.create_index("ix_portfolio_snapshots_captured_at", "portfolio_snapshots", ["captured_at"])
    op.create_index("ix_snapshots_user_captured", "portfolio_snapshots", ["user_id", "captured_at"])
    op.create_index("ix_snapshots_connection_captured", "portfolio_snapshots", ["connection_id", "captured_at"])

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("external_transaction_id", sa.String(length=128), nullable=False),
        sa.Column("transaction_type", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("unit_price", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("fees", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("total_amount", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("transaction_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["broker_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "external_transaction_id", name="uq_transaction_external"),
    )
    op.create_index("ix_transactions_account_id", "transactions", ["account_id"])
    op.create_index("ix_transactions_date", "transactions", ["transaction_date"])


def downgrade() -> None:
    op.drop_table("transactions")
    op.drop_table("portfolio_snapshots")
    op.drop_table("holdings")
    op.drop_table("sync_errors")
    op.drop_table("sync_schedules")
    op.drop_table("sync_runs")
    op.drop_table("broker_accounts")
    op.drop_table("audit_logs")
    op.drop_table("broker_connections")
    op.drop_table("user_sessions")
    op.drop_table("providers")
    op.drop_table("users")
