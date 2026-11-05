"""Provider discovery endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.connectors.registry import get_provider_metadata, list_provider_metadata
from app.core.errors import AppError, ErrorCode
from app.schemas.providers import ProviderListResponse, ProviderResponse

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("", response_model=ProviderListResponse)
def list_providers() -> ProviderListResponse:
    providers = [ProviderResponse.model_validate(m.model_dump()) for m in list_provider_metadata()]
    return ProviderListResponse(providers=providers, count=len(providers))


@router.get("/{provider_id}", response_model=ProviderResponse)
def get_provider(provider_id: str) -> ProviderResponse:
    try:
        meta = get_provider_metadata(provider_id)
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            ErrorCode.PROVIDER_NOT_FOUND,
            f"Provider '{provider_id}' not found",
            status_code=404,
        ) from exc
    return ProviderResponse.model_validate(meta.model_dump())
