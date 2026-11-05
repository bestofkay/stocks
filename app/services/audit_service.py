"""Audit logging with secret redaction."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.encryption import redact_secrets
from app.db.models import AuditLog


def write_audit(
    db: Session,
    *,
    user_id: int | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | int | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        safe_metadata_json=redact_secrets(metadata) if metadata else None,
    )
    db.add(entry)
    db.flush()
    return entry
