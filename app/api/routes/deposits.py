"""Deposit / funding-instruction endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.deposits import DepositInstructionsResponse
from app.services import deposit_service

router = APIRouter(prefix="/deposits", tags=["deposits"])


@router.get("/instructions", response_model=DepositInstructionsResponse)
async def get_deposit_instructions(
    db: DbSession,
    user: CurrentUser,
    connection_id: int = Query(..., description="Broker connection that owns the wallet"),
) -> DepositInstructionsResponse:
    result = await deposit_service.get_deposit_instructions(db, user, connection_id)
    return DepositInstructionsResponse.model_validate(result)
