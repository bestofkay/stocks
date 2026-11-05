"""Live deposit / virtual-account instruction retrieval."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.connectors.models import DataCapability
from app.db.models import User
from app.services.audit_service import write_audit
from app.services.broker_session import authenticate_connection

logger = logging.getLogger(__name__)


async def get_deposit_instructions(
    db: Session,
    user: User,
    connection_id: int,
) -> dict[str, Any]:
    conn, provider, connector, credentials, _auth = await authenticate_connection(
        db,
        user,
        connection_id,
        require_capability=DataCapability.DEPOSIT_INSTRUCTIONS,
    )
    try:
        instructions = await connector.get_deposit_instructions()
        write_audit(
            db,
            user_id=user.id,
            action="deposits.instructions",
            resource_type="broker_connection",
            resource_id=conn.id,
            metadata={"provider": provider.provider_key},
        )
        return {
            "connection_id": conn.id,
            "provider_id": provider.provider_key,
            "bank_name": instructions.bank_name,
            "account_number": instructions.account_number,
            "account_name": instructions.account_name,
            "currency": instructions.currency,
            "provider_reference": instructions.provider_reference,
            "bank_code": instructions.bank_code,
            "raw": instructions.raw,
        }
    finally:
        credentials.clear()
        try:
            await connector.disconnect()
        except Exception:
            logger.exception("Connector disconnect failed after deposit instructions")
