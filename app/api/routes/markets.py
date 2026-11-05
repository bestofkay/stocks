"""Market / instrument listing endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.markets import InstrumentListResponse, MarketListResponse
from app.services import market_service

router = APIRouter(prefix="/markets", tags=["markets"])


@router.get("", response_model=MarketListResponse)
async def list_markets(
    db: DbSession,
    user: CurrentUser,
    connection_id: int = Query(..., description="Broker connection id"),
) -> MarketListResponse:
    result = await market_service.list_markets(db, user, connection_id)
    return MarketListResponse.model_validate(result)


@router.get("/instruments", response_model=InstrumentListResponse)
async def list_instruments(
    db: DbSession,
    user: CurrentUser,
    connection_id: int = Query(..., description="Broker connection id"),
    market_id: str | None = Query(
        default=None,
        description="Exchange/market code (Afrinvestor default: NGX)",
    ),
    search: str | None = Query(
        default=None,
        description="Optional symbol / name search filter",
    ),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=100, ge=1, le=100),
) -> InstrumentListResponse:
    result = await market_service.list_instruments(
        db,
        user,
        connection_id,
        market_id=market_id,
        search=search,
        page=page,
        per_page=per_page,
    )
    return InstrumentListResponse.model_validate(result)
