"""Provider metadata schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.connectors.models import (
    AuthMethod,
    ConnectorType,
    CredentialField,
    DataCapability,
    ImplementationStatus,
)


class ProviderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider_id: str
    provider_name: str
    connector_type: ConnectorType
    implementation_status: ImplementationStatus
    auth_methods: list[AuthMethod]
    required_credential_fields: list[CredentialField]
    optional_credential_fields: list[CredentialField] = Field(default_factory=list)
    mfa_supported: bool = False
    capabilities: list[DataCapability] = Field(default_factory=list)
    approved_domains: list[str] = Field(default_factory=list)
    notes: str | None = None
    operational: bool = False
    registered: bool = True


class ProviderListResponse(BaseModel):
    providers: list[ProviderResponse]
    count: int
