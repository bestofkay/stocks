"""Live market / instrument listing via broker connectors."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.connectors.models import DataCapability
from app.db.models import User
from app.services.audit_service import write_audit
from app.services.broker_session import authenticate_connection

logger = logging.getLogger(__name__)


async def _authenticated_connector(db: Session, user: User, connection_id: int):
    conn, provider, connector, credentials, _auth = await authenticate_connection(
        db,
        user,
        connection_id,
        require_capability=DataCapability.INSTRUMENTS,
    )
    return conn, provider, connector, credentials


async def list_markets(db: Session, user: User, connection_id: int) -> dict[str, Any]:
    conn, provider, connector, credentials = await _authenticated_connector(
        db, user, connection_id
    )
    try:
        markets = await connector.list_markets()
        write_audit(
            db,
            user_id=user.id,
            action="markets.list",
            resource_type="broker_connection",
            resource_id=conn.id,
            metadata={"provider": provider.provider_key, "count": len(markets)},
        )
        return {
            "connection_id": conn.id,
            "provider_id": provider.provider_key,
            "markets": [m.model_dump(mode="json") for m in markets],
        }
    finally:
        credentials.clear()
        try:
            await connector.disconnect()
        except Exception:
            logger.exception("Connector disconnect failed after list_markets")


async def list_instruments(
    db: Session,
    user: User,
    connection_id: int,
    *,
    market_id: str | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 100,
) -> dict[str, Any]:
    conn, provider, connector, credentials = await _authenticated_connector(
        db, user, connection_id
    )
    try:
        result = await connector.list_instruments(
            market_id=market_id,
            search=search,
            page=page,
            per_page=per_page,
        )
        write_audit(
            db,
            user_id=user.id,
            action="markets.instruments",
            resource_type="broker_connection",
            resource_id=conn.id,
            metadata={
                "provider": provider.provider_key,
                "market_id": result.market_id or market_id,
                "search": search,
                "count": len(result.instruments),
            },
        )
        return {
            "connection_id": conn.id,
            "provider_id": provider.provider_key,
            "market_id": result.market_id,
            "page": result.page,
            "per_page": result.per_page,
            "total": result.total,
            "instruments": [i.model_dump(mode="json") for i in result.instruments],
        }
    finally:
        credentials.clear()
        try:
            await connector.disconnect()
        except Exception:
            logger.exception("Connector disconnect failed after list_instruments")
