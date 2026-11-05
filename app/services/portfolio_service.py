"""Portfolio aggregation — currency-separated Decimal totals."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.models import (
    BrokerAccount,
    BrokerConnection,
    Holding,
    PortfolioSnapshot,
    Provider,
    Transaction,
    User,
)
from app.schemas.portfolio import (
    AccountResponse,
    BrokerPortfolioResponse,
    CurrencyTotal,
    HoldingResponse,
    PortfolioSummaryResponse,
    SecurityAggregateResponse,
    SnapshotResponse,
    TransactionResponse,
)


def _sum_optional(values: list[Decimal | None]) -> Decimal | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present, Decimal("0"))


def user_connections_query(db: Session, user: User):
    return db.query(BrokerConnection).filter(BrokerConnection.user_id == user.id)


def list_accounts(
    db: Session,
    user: User,
    *,
    provider_id: str | None = None,
    connection_id: int | None = None,
) -> list[AccountResponse]:
    q = (
        db.query(BrokerAccount, BrokerConnection, Provider)
        .join(BrokerConnection, BrokerAccount.connection_id == BrokerConnection.id)
        .join(Provider, BrokerConnection.provider_id == Provider.id)
        .filter(BrokerConnection.user_id == user.id)
    )
    if connection_id is not None:
        q = q.filter(BrokerAccount.connection_id == connection_id)
    if provider_id is not None:
        q = q.filter(Provider.provider_key == provider_id.lower())
    results = []
    for acct, conn, prov in q.all():
        results.append(
            AccountResponse(
                id=acct.id,
                connection_id=acct.connection_id,
                provider_id=prov.provider_key,
                external_account_id=acct.external_account_id,
                account_name=acct.account_name,
                account_type=acct.account_type,
                currency=acct.currency,
                cash_balance=acct.cash_balance,
                last_seen_at=acct.last_seen_at,
            )
        )
    return results


def list_holdings(
    db: Session,
    user: User,
    *,
    provider_id: str | None = None,
    account_id: int | None = None,
    connection_id: int | None = None,
    symbol: str | None = None,
    asset_type: str | None = None,
    currency: str | None = None,
) -> list[HoldingResponse]:
    q = (
        db.query(Holding, BrokerAccount, BrokerConnection, Provider)
        .join(BrokerAccount, Holding.account_id == BrokerAccount.id)
        .join(BrokerConnection, BrokerAccount.connection_id == BrokerConnection.id)
        .join(Provider, BrokerConnection.provider_id == Provider.id)
        .filter(BrokerConnection.user_id == user.id)
        .filter(Holding.observation_status != "confirmed_absent")
    )
    if account_id is not None:
        q = q.filter(Holding.account_id == account_id)
    if connection_id is not None:
        q = q.filter(BrokerAccount.connection_id == connection_id)
    if provider_id is not None:
        q = q.filter(Provider.provider_key == provider_id.lower())
    if symbol is not None:
        q = q.filter(Holding.symbol == symbol.upper())
    if asset_type is not None:
        q = q.filter(Holding.asset_type == asset_type)
    if currency is not None:
        q = q.filter(Holding.currency == currency.upper())

    results: list[HoldingResponse] = []
    for h, acct, conn, prov in q.all():
        results.append(
            HoldingResponse(
                id=h.id,
                account_id=h.account_id,
                connection_id=conn.id,
                provider_id=prov.provider_key,
                account_name=acct.account_name,
                security_identifier=h.security_identifier,
                symbol=h.symbol,
                security_name=h.security_name,
                isin=h.isin,
                asset_type=h.asset_type,
                quantity=h.quantity,
                average_cost=h.average_cost,
                cost_basis=h.cost_basis,
                current_price=h.current_price,
                market_value=h.market_value,
                unrealized_pnl=h.unrealized_pnl,
                currency=h.currency,
                valuation_timestamp=h.valuation_timestamp,
                observation_status=h.observation_status,
            )
        )
    return results


def portfolio_summary(db: Session, user: User) -> PortfolioSummaryResponse:
    holdings = list_holdings(db, user)
    accounts = list_accounts(db, user)
    connections = user_connections_query(db, user).count()

    by_ccy: dict[str, dict] = defaultdict(
        lambda: {
            "market": [],
            "cost": [],
            "pnl": [],
            "cash": [],
            "count": 0,
        }
    )
    for h in holdings:
        ccy = h.currency or "UNKNOWN"
        by_ccy[ccy]["market"].append(h.market_value)
        by_ccy[ccy]["cost"].append(h.cost_basis)
        by_ccy[ccy]["pnl"].append(h.unrealized_pnl)
        by_ccy[ccy]["count"] += 1

    for acct in accounts:
        if acct.cash_balance is not None:
            ccy = acct.currency or "UNKNOWN"
            by_ccy[ccy]["cash"].append(acct.cash_balance)

    totals = [
        CurrencyTotal(
            currency=ccy,
            total_market_value=_sum_optional(vals["market"]),
            total_cost_basis=_sum_optional(vals["cost"]),
            cash_value=_sum_optional(vals["cash"]),
            unrealized_pnl=_sum_optional(vals["pnl"]),
            holdings_count=vals["count"],
        )
        for ccy, vals in sorted(by_ccy.items())
    ]

    as_of = None
    timestamps = [h.valuation_timestamp for h in holdings if h.valuation_timestamp]
    if timestamps:
        as_of = max(timestamps)

    return PortfolioSummaryResponse(
        by_currency=totals,
        accounts_count=len(accounts),
        holdings_count=len(holdings),
        connections_count=connections,
        as_of=as_of,
    )


def portfolio_by_broker(db: Session, user: User) -> list[BrokerPortfolioResponse]:
    connections = user_connections_query(db, user).all()
    results: list[BrokerPortfolioResponse] = []
    for conn in connections:
        provider = db.get(Provider, conn.provider_id)
        conn_holdings = list_holdings(db, user, connection_id=conn.id)
        by_ccy: dict[str, dict] = defaultdict(lambda: {"market": [], "cost": [], "pnl": [], "cash": [], "count": 0})
        for h in conn_holdings:
            ccy = h.currency or "UNKNOWN"
            by_ccy[ccy]["market"].append(h.market_value)
            by_ccy[ccy]["cost"].append(h.cost_basis)
            by_ccy[ccy]["pnl"].append(h.unrealized_pnl)
            by_ccy[ccy]["count"] += 1
        accounts = list_accounts(db, user, connection_id=conn.id)
        for acct in accounts:
            if acct.cash_balance is not None:
                ccy = acct.currency or "UNKNOWN"
                by_ccy[ccy]["cash"].append(acct.cash_balance)
        totals = [
            CurrencyTotal(
                currency=ccy,
                total_market_value=_sum_optional(v["market"]),
                total_cost_basis=_sum_optional(v["cost"]),
                cash_value=_sum_optional(v["cash"]),
                unrealized_pnl=_sum_optional(v["pnl"]),
                holdings_count=v["count"],
            )
            for ccy, v in sorted(by_ccy.items())
        ]
        results.append(
            BrokerPortfolioResponse(
                connection_id=conn.id,
                provider_id=provider.provider_key if provider else str(conn.provider_id),
                display_name=conn.display_name,
                by_currency=totals,
                holdings=conn_holdings,
            )
        )
    return results


def portfolio_by_security(db: Session, user: User) -> list[SecurityAggregateResponse]:
    """Aggregate equivalent securities. Prefer ISIN; do not merge on symbol alone across currencies."""
    holdings = list_holdings(db, user)
    groups: dict[str, list[HoldingResponse]] = defaultdict(list)

    for h in holdings:
        if h.isin:
            key = f"isin:{h.isin}"
        elif h.symbol and h.currency:
            key = f"symbol:{h.symbol}:{h.currency}"
        elif h.symbol:
            key = f"symbol:{h.symbol}:UNKNOWN"
        else:
            key = h.security_identifier
        groups[key].append(h)

    results: list[SecurityAggregateResponse] = []
    for key, positions in sorted(groups.items()):
        currencies = {p.currency for p in positions if p.currency}
        currency = next(iter(currencies)) if len(currencies) == 1 else None
        # Only sum quantities when all share the same currency (or all unknown)
        qty = None
        mv = None
        if len(currencies) <= 1:
            qty = _sum_optional([p.quantity for p in positions])
            mv = _sum_optional([p.market_value for p in positions])
        results.append(
            SecurityAggregateResponse(
                security_key=key,
                symbol=positions[0].symbol,
                security_name=positions[0].security_name,
                isin=positions[0].isin,
                currency=currency,
                total_quantity=qty,
                total_market_value=mv,
                positions=positions,
            )
        )
    return results


def portfolio_history(
    db: Session,
    user: User,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    provider_id: str | None = None,
    account_id: int | None = None,
    currency: str | None = None,
    connection_id: int | None = None,
) -> list[SnapshotResponse]:
    q = db.query(PortfolioSnapshot).filter(PortfolioSnapshot.user_id == user.id)
    if date_from is not None:
        q = q.filter(PortfolioSnapshot.captured_at >= date_from)
    if date_to is not None:
        q = q.filter(PortfolioSnapshot.captured_at <= date_to)
    if account_id is not None:
        q = q.filter(PortfolioSnapshot.account_id == account_id)
    if currency is not None:
        q = q.filter(PortfolioSnapshot.currency == currency.upper())
    if connection_id is not None:
        q = q.filter(PortfolioSnapshot.connection_id == connection_id)
    if provider_id is not None:
        q = (
            q.join(BrokerConnection, PortfolioSnapshot.connection_id == BrokerConnection.id)
            .join(Provider, BrokerConnection.provider_id == Provider.id)
            .filter(Provider.provider_key == provider_id.lower())
        )
    rows = q.order_by(PortfolioSnapshot.captured_at.desc()).limit(500).all()
    return [SnapshotResponse.model_validate(r) for r in rows]


def list_transactions(
    db: Session,
    user: User,
    *,
    account_id: int | None = None,
    symbol: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[TransactionResponse]:
    q = (
        db.query(Transaction)
        .join(BrokerAccount, Transaction.account_id == BrokerAccount.id)
        .join(BrokerConnection, BrokerAccount.connection_id == BrokerConnection.id)
        .filter(BrokerConnection.user_id == user.id)
    )
    if account_id is not None:
        q = q.filter(Transaction.account_id == account_id)
    if symbol is not None:
        q = q.filter(Transaction.symbol == symbol.upper())
    if date_from is not None:
        q = q.filter(Transaction.transaction_date >= date_from)
    if date_to is not None:
        q = q.filter(Transaction.transaction_date <= date_to)
    rows = q.order_by(Transaction.transaction_date.desc()).limit(500).all()
    return [TransactionResponse.model_validate(r) for r in rows]
