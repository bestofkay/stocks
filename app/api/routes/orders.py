"""Order placement endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.orders import OrderCreateRequest, OrderResponse
from app.services import order_service

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderResponse, status_code=201)
async def place_order(
    body: OrderCreateRequest,
    db: DbSession,
    user: CurrentUser,
) -> OrderResponse:
    result = await order_service.place_order(
        db,
        user,
        connection_id=body.connection_id,
        side=body.side,
        symbol=body.symbol,
        quantity=body.quantity,
        amount=body.amount,
        account_external_id=body.account_external_id,
        order_type=body.order_type,
        limit_price=body.limit_price,
        tif=body.tif,
        expires=body.expires,
        pin=body.pin,
    )
    return OrderResponse.model_validate(result)
