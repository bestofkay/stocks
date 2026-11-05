"""Sync provider metadata from connector registry into MySQL."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.connectors.registry import discover_connectors, list_provider_metadata
from app.db.models import Provider

logger = logging.getLogger(__name__)


def sync_providers_to_db(db: Session) -> list[Provider]:
    discover_connectors()
    synced: list[Provider] = []
    for meta in list_provider_metadata():
        row = db.query(Provider).filter(Provider.provider_key == meta.provider_id).first()
        payload = {
            "name": meta.provider_name,
            "connector_type": meta.connector_type.value,
            "enabled": True,
            "implementation_status": meta.implementation_status.value,
            "capabilities_json": {
                "auth_methods": [m.value for m in meta.auth_methods],
                "capabilities": [c.value for c in meta.capabilities],
                "mfa_supported": meta.mfa_supported,
                "approved_domains": meta.approved_domains,
                "operational": meta.operational,
                "required_credential_fields": [f.model_dump() for f in meta.required_credential_fields],
                "optional_credential_fields": [f.model_dump() for f in meta.optional_credential_fields],
            },
            "notes": meta.notes,
        }
        if row is None:
            row = Provider(provider_key=meta.provider_id, **payload)
            db.add(row)
        else:
            for key, value in payload.items():
                setattr(row, key, value)
        synced.append(row)
    db.flush()
    for row in synced:
        db.refresh(row)
    logger.info("Synced %d providers to database", len(synced))
    return synced


def get_provider_by_key(db: Session, provider_key: str) -> Provider | None:
    return db.query(Provider).filter(Provider.provider_key == provider_key.lower()).first()
