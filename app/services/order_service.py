"""Live broker order placement (buy / sell)."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.connectors.models import DataCapability, OrderRequest, OrderSide, OrderType
from app.core.errors import AppError, ErrorCode
from app.db.models import User
from app.services.audit_service import write_audit
from app.services.broker_session import authenticate_connection

logger = logging.getLogger(__name__)


async def place_order(
    db: Session,
    user: User,
    *,
    connection_id: int,
    side: str,
    symbol: str,
    quantity: Decimal | None = None,
    amount: Decimal | None = None,
    account_external_id: str | None = None,
    order_type: str = "market",
    limit_price: Decimal | None = None,
    tif: str = "DAY",
    expires: str | None = None,
    pin: str | None = None,
) -> dict[str, Any]:
    if quantity is None and amount is not None:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "amount-based orders are not supported for this provider yet; pass quantity",
            status_code=422,
        )

    order = OrderRequest(
        side=OrderSide(side),
        symbol=symbol,
        quantity=quantity,
        amount=amount,
        account_external_id=account_external_id,
        order_type=OrderType(order_type),
        limit_price=limit_price,
        tif=tif,
        expires=expires,
        pin=pin,
    )

    conn, provider, connector, credentials, _auth = await authenticate_connection(
        db,
        user,
        connection_id,
        require_capability=DataCapability.ORDERS,
    )
    try:
        result = await connector.place_order(order)
        write_audit(
            db,
            user_id=user.id,
            action="orders.place",
            resource_type="broker_connection",
            resource_id=conn.id,
            metadata={
                "provider": provider.provider_key,
                "side": side,
                "symbol": symbol,
                "status": result.status,
                "external_order_id": result.external_order_id,
            },
        )
        return {
            "connection_id": conn.id,
            "provider_id": provider.provider_key,
            "external_order_id": result.external_order_id,
            "status": result.status,
            "side": result.side.value if result.side else side,
            "symbol": result.symbol or symbol,
            "filled_qty": result.filled_qty,
            "message": result.message,
            "raw": result.raw,
        }
    finally:
        credentials.clear()
        if pin:
            order.pin = None
        try:
            await connector.disconnect()
        except Exception:
            logger.exception("Connector disconnect failed after place_order")
