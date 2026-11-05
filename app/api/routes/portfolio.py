"""Portfolio aggregation endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.portfolio import (
    AccountResponse,
    BrokerPortfolioResponse,
    HoldingResponse,
    PortfolioSummaryResponse,
    SecurityAggregateResponse,
    SnapshotResponse,
    TransactionResponse,
)
from app.services import portfolio_service

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/summary", response_model=PortfolioSummaryResponse)
def summary(db: DbSession, user: CurrentUser) -> PortfolioSummaryResponse:
    return portfolio_service.portfolio_summary(db, user)


@router.get("/holdings", response_model=list[HoldingResponse])
def holdings(
    db: DbSession,
    user: CurrentUser,
    provider: str | None = Query(default=None, alias="provider"),
    account_id: int | None = None,
    connection_id: int | None = None,
    symbol: str | None = None,
    asset_type: str | None = None,
    currency: str | None = None,
) -> list[HoldingResponse]:
    return portfolio_service.list_holdings(
        db,
        user,
        provider_id=provider,
        account_id=account_id,
        connection_id=connection_id,
        symbol=symbol,
        asset_type=asset_type,
        currency=currency,
    )


@router.get("/accounts", response_model=list[AccountResponse])
def accounts(
    db: DbSession,
    user: CurrentUser,
    provider: str | None = None,
    connection_id: int | None = None,
) -> list[AccountResponse]:
    return portfolio_service.list_accounts(
        db, user, provider_id=provider, connection_id=connection_id
    )


@router.get("/by-broker", response_model=list[BrokerPortfolioResponse])
def by_broker(db: DbSession, user: CurrentUser) -> list[BrokerPortfolioResponse]:
    return portfolio_service.portfolio_by_broker(db, user)


@router.get("/by-security", response_model=list[SecurityAggregateResponse])
def by_security(db: DbSession, user: CurrentUser) -> list[SecurityAggregateResponse]:
    return portfolio_service.portfolio_by_security(db, user)


@router.get("/history", response_model=list[SnapshotResponse])
def history(
    db: DbSession,
    user: CurrentUser,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    provider: str | None = None,
    account_id: int | None = None,
    connection_id: int | None = None,
    currency: str | None = None,
) -> list[SnapshotResponse]:
    return portfolio_service.portfolio_history(
        db,
        user,
        date_from=date_from,
        date_to=date_to,
        provider_id=provider,
        account_id=account_id,
        connection_id=connection_id,
        currency=currency,
    )


@router.get("/transactions", response_model=list[TransactionResponse])
def transactions(
    db: DbSession,
    user: CurrentUser,
    account_id: int | None = None,
    symbol: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[TransactionResponse]:
    return portfolio_service.list_transactions(
        db,
        user,
        account_id=account_id,
        symbol=symbol,
        date_from=date_from,
        date_to=date_to,
    )
