"""Example authenticated-HTTP connector scaffold.

Use this package as a template when adding a new broker. It is registered
but intentionally non-operational until real endpoints are verified.
"""

from __future__ import annotations

from typing import Any

from app.connectors.base import BrokerConnector
from app.connectors.models import (
    AuthMethod,
    AuthResult,
    ConnectorType,
    CredentialField,
    DataCapability,
    ImplementationStatus,
    NormalizedAccount,
    NormalizedHolding,
    ProviderMetadata,
)
from app.connectors.registry import register_connector
from app.core.errors import AppError, ErrorCode


@register_connector("example_broker")
class ExampleBrokerConnector(BrokerConnector):
    provider_id = "example_broker"
    provider_name = "Example Broker"

    @classmethod
    def metadata(cls) -> ProviderMetadata:
        return ProviderMetadata(
            provider_id="example_broker",
            provider_name="Example Broker",
            connector_type=ConnectorType.AUTHENTICATED_HTTP,
            implementation_status=ImplementationStatus.REGISTERED,
            auth_methods=[AuthMethod.API_KEY],
            required_credential_fields=[
                CredentialField(
                    name="api_key",
                    label="API Key",
                    field_type="password",
                    required=True,
                    secret=True,
                ),
            ],
            optional_credential_fields=[
                CredentialField(
                    name="api_secret",
                    label="API Secret",
                    field_type="password",
                    required=False,
                    secret=True,
                ),
            ],
            mfa_supported=False,
            capabilities=[DataCapability.ACCOUNTS, DataCapability.HOLDINGS],
            approved_domains=["api.example-broker.invalid"],
            notes=(
                "Scaffold only. Replace authenticate/get_accounts/get_holdings with "
                "verified API calls before marking operational."
            ),
            operational=False,
        )

    async def authenticate(
        self,
        credentials: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> AuthResult:
        raise AppError(
            ErrorCode.CONNECTOR_NOT_IMPLEMENTED,
            "example_broker is a scaffold; implement real API authentication before use",
            status_code=501,
        )

    async def get_accounts(self) -> list[NormalizedAccount]:
        raise AppError(
            ErrorCode.CONNECTOR_NOT_IMPLEMENTED,
            "example_broker get_accounts not implemented",
            status_code=501,
        )

    async def get_holdings(self, account: NormalizedAccount) -> list[NormalizedHolding]:
        raise AppError(
            ErrorCode.CONNECTOR_NOT_IMPLEMENTED,
            "example_broker get_holdings not implemented",
            status_code=501,
        )

    async def disconnect(self) -> None:
        return None
