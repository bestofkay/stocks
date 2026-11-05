"""Broker connection management with encrypted credentials."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.connectors.models import CredentialField, ImplementationStatus
from app.connectors.registry import create_connector, get_provider_metadata
from app.core.encryption import get_encryption_service
from app.core.errors import AppError, ErrorCode, not_found
from app.db.models import BrokerConnection, Provider, User
from app.services.audit_service import write_audit
from app.services.provider_service import get_provider_by_key


def _aad_for_connection(user_id: int, provider_key: str) -> bytes:
    return f"user:{user_id}:provider:{provider_key}".encode("utf-8")


def validate_credentials_against_schema(
    credentials: dict[str, Any],
    required: list[CredentialField],
    optional: list[CredentialField],
) -> None:
    allowed = {f.name for f in required} | {f.name for f in optional}
    # Also accept "email" as alias for username when username is required
    unknown = set(credentials.keys()) - allowed - {"email"}
    if unknown:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"Unexpected credential fields: {sorted(unknown)}",
            status_code=422,
        )
    for field in required:
        value = credentials.get(field.name)
        if field.name == "username" and value is None:
            value = credentials.get("email")
        if value is None or (isinstance(value, str) and not value.strip()):
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                f"Missing required credential field: {field.name}",
                status_code=422,
            )


def create_connection(
    db: Session,
    user: User,
    *,
    provider_id: str,
    display_name: str,
    credentials: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> BrokerConnection:
    meta = get_provider_metadata(provider_id)
    if meta.implementation_status == ImplementationStatus.REGISTERED and not meta.operational:
        # Still allow creating connections for scaffolded connectors
        pass

    validate_credentials_against_schema(
        credentials,
        meta.required_credential_fields,
        meta.optional_credential_fields,
    )

    provider = get_provider_by_key(db, provider_id)
    if provider is None:
        raise not_found(ErrorCode.PROVIDER_NOT_FOUND, f"Provider '{provider_id}' not found in database")

    enc = get_encryption_service()
    aad = _aad_for_connection(user.id, provider.provider_key)
    creds_encrypted = enc.encrypt_dict(credentials, aad=aad)
    opts_encrypted = None
    if options:
        opts_encrypted = enc.encrypt_dict(options, aad=aad + b":options")

    conn = BrokerConnection(
        user_id=user.id,
        provider_id=provider.id,
        display_name=display_name,
        credentials_encrypted=creds_encrypted,
        options_encrypted=opts_encrypted,
        status="configured",
    )
    db.add(conn)
    db.flush()
    write_audit(
        db,
        user_id=user.id,
        action="connection.create",
        resource_type="broker_connection",
        resource_id=conn.id,
        metadata={"provider_id": provider.provider_key, "display_name": display_name},
    )
    db.commit()
    db.refresh(conn)
    return conn


def list_connections(db: Session, user: User) -> list[BrokerConnection]:
    return (
        db.query(BrokerConnection)
        .filter(BrokerConnection.user_id == user.id)
        .order_by(BrokerConnection.id.desc())
        .all()
    )


def get_connection(db: Session, user: User, connection_id: int) -> BrokerConnection:
    conn = db.get(BrokerConnection, connection_id)
    if conn is None or conn.user_id != user.id:
        raise not_found(ErrorCode.CONNECTION_NOT_FOUND, "Connection not found")
    return conn


def update_connection(
    db: Session,
    user: User,
    connection_id: int,
    *,
    display_name: str | None = None,
    credentials: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    status: str | None = None,
) -> BrokerConnection:
    conn = get_connection(db, user, connection_id)
    provider = db.get(Provider, conn.provider_id)
    assert provider is not None

    if display_name is not None:
        conn.display_name = display_name
    if status is not None:
        conn.status = status

    enc = get_encryption_service()
    aad = _aad_for_connection(user.id, provider.provider_key)

    if credentials is not None:
        meta = get_provider_metadata(provider.provider_key)
        validate_credentials_against_schema(
            credentials,
            meta.required_credential_fields,
            meta.optional_credential_fields,
        )
        conn.credentials_encrypted = enc.encrypt_dict(credentials, aad=aad)
        # Clear any persisted session when credentials change
        conn.session_state_encrypted = None

    if options is not None:
        conn.options_encrypted = enc.encrypt_dict(options, aad=aad + b":options")

    write_audit(
        db,
        user_id=user.id,
        action="connection.update",
        resource_type="broker_connection",
        resource_id=conn.id,
        metadata={
            "updated_credentials": credentials is not None,
            "updated_options": options is not None,
        },
    )
    db.commit()
    db.refresh(conn)
    return conn


def delete_connection(db: Session, user: User, connection_id: int) -> None:
    conn = get_connection(db, user, connection_id)
    write_audit(
        db,
        user_id=user.id,
        action="connection.delete",
        resource_type="broker_connection",
        resource_id=conn.id,
    )
    db.delete(conn)
    db.commit()


def decrypt_credentials(db: Session, conn: BrokerConnection) -> dict[str, Any]:
    provider = db.get(Provider, conn.provider_id)
    assert provider is not None
    enc = get_encryption_service()
    aad = _aad_for_connection(conn.user_id, provider.provider_key)
    return enc.decrypt_dict(conn.credentials_encrypted, aad=aad)


def decrypt_options(db: Session, conn: BrokerConnection) -> dict[str, Any]:
    if not conn.options_encrypted:
        return {}
    provider = db.get(Provider, conn.provider_id)
    assert provider is not None
    enc = get_encryption_service()
    aad = _aad_for_connection(conn.user_id, provider.provider_key) + b":options"
    return enc.decrypt_dict(conn.options_encrypted, aad=aad)


def apply_options_patch(
    db: Session,
    conn: BrokerConnection,
    patch: dict[str, Any] | None,
    *,
    drop_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Merge ``patch`` into encrypted connection options and persist.

    Used after connector auth to store Playwright ``storage_state`` (and similar
    session material) without exposing it on API responses.
    """
    if not patch and not drop_keys:
        return decrypt_options(db, conn)

    provider = db.get(Provider, conn.provider_id)
    assert provider is not None
    enc = get_encryption_service()
    aad = _aad_for_connection(conn.user_id, provider.provider_key) + b":options"
    options = decrypt_options(db, conn)
    if drop_keys:
        for key in drop_keys:
            options.pop(key, None)
    if patch:
        options.update(patch)
    conn.options_encrypted = enc.encrypt_dict(options, aad=aad) if options else None
    db.commit()
    return options


def connection_has_broker_session(db: Session, conn: BrokerConnection) -> bool:
    """Non-secret flag: whether an encrypted broker browser session is stored."""
    options = decrypt_options(db, conn)
    state = options.get("storage_state")
    if isinstance(state, dict) and bool(state.get("cookies")):
        return True
    # HTTP connectors persist bearer/refresh tokens under ``api_session``
    api_session = options.get("api_session")
    return isinstance(api_session, dict) and bool(api_session.get("token"))


def connection_to_response(db: Session, conn: BrokerConnection) -> dict[str, Any]:
    provider = db.get(Provider, conn.provider_id)
    return {
        "id": conn.id,
        "provider_id": provider.provider_key if provider else str(conn.provider_id),
        "provider_key": provider.provider_key if provider else None,
        "display_name": conn.display_name,
        "status": conn.status,
        "last_sync_at": conn.last_sync_at,
        "last_successful_sync_at": conn.last_successful_sync_at,
        "last_error_code": conn.last_error_code,
        "has_credentials": bool(conn.credentials_encrypted),
        "has_broker_session": connection_has_broker_session(db, conn),
        "created_at": conn.created_at,
        "updated_at": conn.updated_at,
    }


async def test_connection(db: Session, user: User, connection_id: int) -> dict[str, Any]:
    conn = get_connection(db, user, connection_id)
    provider = db.get(Provider, conn.provider_id)
    assert provider is not None
    connector = create_connector(provider.provider_key)
    try:
        # Prefer lightweight page reachability test when available
        if hasattr(connector, "test_connection"):
            result = await connector.test_connection()
        else:
            creds = decrypt_credentials(db, conn)
            opts = decrypt_options(db, conn)
            conn.status = "authenticating"
            db.commit()
            result = await connector.authenticate(creds, opts)
            # Drop plaintext refs
            creds.clear()
            if getattr(result, "options_patch", None):
                apply_options_patch(db, conn, result.options_patch)
        conn.status = "configured" if result.success else "authentication_failed"
        if not result.success and result.status == "mfa_required":
            conn.status = "mfa_required"
        db.commit()
        write_audit(
            db,
            user_id=user.id,
            action="connection.test",
            resource_type="broker_connection",
            resource_id=conn.id,
            metadata={"success": result.success, "status": result.status},
        )
        return {
            "success": result.success,
            "status": result.status,
            "message": result.message,
            "has_broker_session": connection_has_broker_session(db, conn),
        }
    finally:
        await connector.disconnect()


async def submit_mfa(
    db: Session,
    user: User,
    connection_id: int,
    code: str,
) -> dict[str, Any]:
    """Complete MFA for a connection. Requires an in-progress sync that left MFA pending.

    For browser connectors, MFA must be submitted during an active sync session.
    This endpoint stores the OTP encrypted briefly is NOT done — instead clients
    should include otp in credentials and re-run sync, OR use the active sync flow.

    Practical approach: merge otp into credentials temporarily for the next sync
    without persisting the OTP at rest.
    """
    conn = get_connection(db, user, connection_id)
    provider = db.get(Provider, conn.provider_id)
    assert provider is not None

    # Attach OTP for next authentication attempt only (encrypted ephemeral options field)
    enc = get_encryption_service()
    aad = _aad_for_connection(user.id, provider.provider_key) + b":options"
    options = decrypt_options(db, conn)
    options["pending_otp"] = code
    conn.options_encrypted = enc.encrypt_dict(options, aad=aad)
    conn.status = "mfa_required"
    write_audit(
        db,
        user_id=user.id,
        action="connection.mfa_submitted",
        resource_type="broker_connection",
        resource_id=conn.id,
        metadata={"otp_provided": True},
    )
    db.commit()
    return {
        "success": True,
        "status": "mfa_required",
        "message": "OTP stored for next sync attempt. Call POST .../sync to continue.",
    }
