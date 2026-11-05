"""Abstract broker connector interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.connectors.models import (
    AuthResult,
    DepositInstructions,
    InstrumentListResult,
    MarketInfo,
    NormalizedAccount,
    NormalizedHolding,
    NormalizedTransaction,
    OrderRequest,
    OrderResult,
    PortfolioSummary,
    ProviderMetadata,
)


class BrokerConnector(ABC):
    """Typed asynchronous connector contract.

    Provider-specific login selectors, URLs, and parsing live only in
    connector packages — never in portfolio core services.
    """

    provider_id: str
    provider_name: str

    @classmethod
    @abstractmethod
    def metadata(cls) -> ProviderMetadata:
        """Declare capabilities, credential schema, and implementation status."""

    @abstractmethod
    async def authenticate(
        self,
        credentials: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> AuthResult:
        """Authenticate against the provider. Never bypass MFA/CAPTCHA."""

    @abstractmethod
    async def get_accounts(self) -> list[NormalizedAccount]:
        ...

    @abstractmethod
    async def get_holdings(self, account: NormalizedAccount) -> list[NormalizedHolding]:
        ...

    async def get_cash_balance(self, account: NormalizedAccount) -> Any:
        """Return Decimal cash balance or None if unsupported/unknown."""
        return None

    async def get_transactions(
        self, account: NormalizedAccount
    ) -> list[NormalizedTransaction]:
        return []

    async def get_portfolio_summary(
        self, account: NormalizedAccount
    ) -> PortfolioSummary | None:
        return None

    async def submit_mfa(self, code: str, challenge: dict[str, Any] | None = None) -> AuthResult:
        """Complete MFA when status is mfa_required. Default: not supported."""
        raise NotImplementedError(f"{self.provider_id} does not support MFA submission via API")

    async def test_connection(self) -> AuthResult:
        """Lightweight connectivity check after credentials are configured."""
        raise NotImplementedError

    async def get_deposit_instructions(self) -> DepositInstructions:
        """Return dedicated bank / virtual-account funding details.

        Default: not supported. Connectors that advertise
        ``DataCapability.DEPOSIT_INSTRUCTIONS`` must override.
        """
        raise NotImplementedError(
            f"{self.provider_id} does not support deposit instructions"
        )

    async def place_order(self, order: OrderRequest) -> OrderResult:
        """Place a buy or sell order with the broker.

        Default: not supported. Connectors that advertise
        ``DataCapability.ORDERS`` must override.
        """
        raise NotImplementedError(f"{self.provider_id} does not support order placement")

    async def list_markets(self) -> list[MarketInfo]:
        """Return tradeable markets/exchanges for this connection.

        Default: not supported. Connectors that advertise
        ``DataCapability.INSTRUMENTS`` should override.
        """
        raise NotImplementedError(f"{self.provider_id} does not support market listing")

    async def list_instruments(
        self,
        *,
        market_id: str | None = None,
        search: str | None = None,
        page: int = 1,
        per_page: int = 100,
    ) -> InstrumentListResult:
        """Return tradeable instruments (optionally filtered/searched).

        Default: not supported. Connectors that advertise
        ``DataCapability.INSTRUMENTS`` must override.
        """
        raise NotImplementedError(f"{self.provider_id} does not support instrument listing")

    def supports_concurrent_sync(self) -> bool:
        return False

    @abstractmethod
    async def disconnect(self) -> None:
        """Close browser contexts, HTTP clients, and clear ephemeral secrets."""
