"""Shared helpers for broker auth + encrypted session persistence."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.connectors.base import BrokerConnector
from app.connectors.models import AuthResult
from app.connectors.registry import create_connector, get_provider_metadata
from app.core.errors import AppError, ErrorCode
from app.db.models import BrokerConnection, Provider, User
from app.services.connection_service import (
    apply_options_patch,
    decrypt_credentials,
    decrypt_options,
    get_connection,
)

logger = logging.getLogger(__name__)


def _auth_error(auth: AuthResult) -> AppError:
    if auth.status == "mfa_required":
        return AppError(ErrorCode.MFA_REQUIRED, auth.message or "MFA required", status_code=401)
    if auth.status == "captcha_required":
        return AppError(
            ErrorCode.CAPTCHA_REQUIRED,
            auth.message or "CAPTCHA required",
            status_code=401,
        )
    if auth.status == "failed":
        return AppError(
            ErrorCode.INVALID_CREDENTIALS,
            auth.message or "Broker authentication failed",
            status_code=401,
        )
    return AppError(
        ErrorCode.PROVIDER_UNAVAILABLE,
        auth.message or "Broker authentication failed",
        status_code=503,
    )


async def authenticate_connection(
    db: Session,
    user: User,
    connection_id: int,
    *,
    require_capability: Any | None = None,
) -> tuple[BrokerConnection, Provider, BrokerConnector, dict[str, Any], AuthResult]:
    """Decrypt creds, authenticate, persist any ``options_patch`` (e.g. storage_state).

    Returns ``(conn, provider, connector, credentials, auth)``. Caller must
    ``credentials.clear()`` and ``await connector.disconnect()`` in a finally block.
    """
    conn = get_connection(db, user, connection_id)
    provider = db.get(Provider, conn.provider_id)
    assert provider is not None

    if require_capability is not None:
        meta = get_provider_metadata(provider.provider_key)
        if require_capability not in meta.capabilities:
            raise AppError(
                ErrorCode.CONNECTOR_NOT_IMPLEMENTED,
                f"Provider '{provider.provider_key}' does not support {require_capability}",
                status_code=501,
            )

    connector = create_connector(provider.provider_key)
    credentials = decrypt_credentials(db, conn)
    options = decrypt_options(db, conn)
    try:
        auth = await connector.authenticate(credentials, options)
        if auth.options_patch:
            apply_options_patch(db, conn, auth.options_patch)
        if not auth.success:
            raise _auth_error(auth)
        return conn, provider, connector, credentials, auth
    except Exception:
        credentials.clear()
        try:
            await connector.disconnect()
        except Exception:
            logger.exception("Connector disconnect failed after auth error")
        raise
