"""Synchronization orchestration — provider-agnostic."""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.connectors.models import NormalizedAccount, NormalizedHolding
from app.connectors.registry import create_connector
from app.core.errors import AppError, ErrorCode, conflict, not_found
from app.core.security import utcnow
from app.db.database import get_session_factory
from app.db.models import (
    BrokerAccount,
    BrokerConnection,
    Holding,
    PortfolioSnapshot,
    Provider,
    SyncError,
    SyncRun,
    Transaction,
    User,
)
from app.services.audit_service import write_audit
from app.services.connection_service import (
    apply_options_patch,
    decrypt_credentials,
    decrypt_options,
    get_connection,
)

logger = logging.getLogger(__name__)

# In-process lock to prevent concurrent sync of the same connection
_sync_locks: dict[int, asyncio.Lock] = {}
_running_tasks: dict[int, asyncio.Task] = {}


def _lock_for(connection_id: int) -> asyncio.Lock:
    if connection_id not in _sync_locks:
        _sync_locks[connection_id] = asyncio.Lock()
    return _sync_locks[connection_id]


ACTIVE_SYNC_STATUSES = frozenset({"queued", "running", "waiting_for_user"})


def get_sync_run(db: Session, user: User, sync_id: int) -> SyncRun:
    run = db.get(SyncRun, sync_id)
    if run is None:
        raise not_found(ErrorCode.SYNC_NOT_FOUND, "Sync run not found")
    conn = db.get(BrokerConnection, run.connection_id)
    if conn is None or conn.user_id != user.id:
        raise not_found(ErrorCode.SYNC_NOT_FOUND, "Sync run not found")
    return run


def list_syncs(
    db: Session,
    user: User,
    *,
    connection_id: int | None = None,
    limit: int = 50,
) -> list[SyncRun]:
    q = (
        db.query(SyncRun)
        .join(BrokerConnection, SyncRun.connection_id == BrokerConnection.id)
        .filter(BrokerConnection.user_id == user.id)
    )
    if connection_id is not None:
        q = q.filter(SyncRun.connection_id == connection_id)
    return q.order_by(SyncRun.id.desc()).limit(limit).all()


def enqueue_sync(db: Session, user: User, connection_id: int) -> SyncRun:
    conn = get_connection(db, user, connection_id)
    active = (
        db.query(SyncRun)
        .filter(
            SyncRun.connection_id == conn.id,
            SyncRun.status.in_(list(ACTIVE_SYNC_STATUSES)),
        )
        .first()
    )
    if active:
        raise conflict(
            ErrorCode.SYNC_ALREADY_RUNNING,
            f"Synchronization already in progress (sync_id={active.id})",
        )

    run = SyncRun(
        connection_id=conn.id,
        status="queued",
        current_step="queued",
        progress=Decimal("0"),
    )
    db.add(run)
    conn.status = "syncing"
    db.flush()
    write_audit(
        db,
        user_id=user.id,
        action="sync.enqueue",
        resource_type="sync_run",
        resource_id=run.id,
        metadata={"connection_id": conn.id},
    )
    db.commit()
    db.refresh(run)
    return run


def try_enqueue_sync(db: Session, user: User, connection_id: int) -> SyncRun | None:
    """Enqueue a sync when idle; return None if one is already active (no error)."""
    conn = get_connection(db, user, connection_id)
    active = (
        db.query(SyncRun)
        .filter(
            SyncRun.connection_id == conn.id,
            SyncRun.status.in_(list(ACTIVE_SYNC_STATUSES)),
        )
        .first()
    )
    if active:
        return None
    return enqueue_sync(db, user, connection_id)


def enqueue_idle_syncs_for_user(db: Session, user: User) -> list[SyncRun]:
    """Queue portfolio refresh for every connection that is not already syncing.

    Used after login so collateral assets in the DB stay current without blocking auth.
    """
    connections = (
        db.query(BrokerConnection)
        .filter(BrokerConnection.user_id == user.id)
        .order_by(BrokerConnection.id.asc())
        .all()
    )
    runs: list[SyncRun] = []
    for conn in connections:
        run = try_enqueue_sync(db, user, conn.id)
        if run is not None:
            runs.append(run)
    return runs


async def start_sync_background(sync_id: int) -> None:
    """Launch sync in a background task. Limitations: in-process only;
    jobs are lost on process restart; not safe across multiple workers
    without an external lock (MySQL row lock used inside run).
    """
    if sync_id in _running_tasks and not _running_tasks[sync_id].done():
        return
    task = asyncio.create_task(_run_sync_job(sync_id), name=f"sync-{sync_id}")
    _running_tasks[sync_id] = task

    def _cleanup(t: asyncio.Task) -> None:
        _running_tasks.pop(sync_id, None)
        if t.cancelled():
            return
        try:
            exc = t.exception()
        except asyncio.CancelledError:
            return
        if exc:
            logger.exception("Background sync %s crashed", sync_id, exc_info=exc)

    task.add_done_callback(_cleanup)


def cancel_sync(db: Session, user: User, sync_id: int) -> SyncRun:
    """Cancel a queued/running/waiting sync. Best-effort for in-process tasks."""
    run = get_sync_run(db, user, sync_id)
    if run.status not in ACTIVE_SYNC_STATUSES:
        raise AppError(
            ErrorCode.CONFLICT,
            f"Sync run is already {run.status} and cannot be cancelled",
            status_code=409,
        )

    task = _running_tasks.get(sync_id)
    if task and not task.done():
        task.cancel()

    run.status = "cancelled"
    run.current_step = "cancelled"
    run.completed_at = utcnow()
    run.safe_error_message = "Cancelled by user"
    conn = db.get(BrokerConnection, run.connection_id)
    if conn and conn.status == "syncing":
        conn.status = "configured"
    write_audit(
        db,
        user_id=user.id,
        action="sync.cancelled",
        resource_type="sync_run",
        resource_id=run.id,
    )
    db.commit()
    db.refresh(run)
    return run


def recover_interrupted_syncs(db: Session) -> int:
    """Mark in-flight syncs as failed after process restart (scheduler recovery)."""
    interrupted = (
        db.query(SyncRun)
        .filter(SyncRun.status.in_(["queued", "running"]))
        .all()
    )
    count = 0
    now = utcnow()
    for run in interrupted:
        run.status = "failed"
        run.error_code = ErrorCode.INTERNAL_ERROR.value
        run.safe_error_message = "Interrupted by application restart"
        run.completed_at = now
        run.current_step = "interrupted"
        conn = db.get(BrokerConnection, run.connection_id)
        if conn and conn.status in {"syncing", "authenticating"}:
            conn.status = "configured"
            conn.last_error_code = ErrorCode.INTERNAL_ERROR.value
        count += 1
    if count:
        db.commit()
        logger.warning("Recovered %d interrupted sync run(s) after restart", count)
    return count


async def shutdown_sync_tasks(*, timeout_seconds: float = 10.0) -> None:
    """Cancel in-flight background sync tasks during graceful shutdown."""
    tasks = [t for t in _running_tasks.values() if not t.done()]
    if not tasks:
        return
    logger.info("Cancelling %d in-flight sync task(s)", len(tasks))
    for task in tasks:
        task.cancel()
    await asyncio.wait(tasks, timeout=timeout_seconds)


async def _run_sync_job(sync_id: int) -> None:
    factory = get_session_factory()
    db = factory()
    try:
        run = db.get(SyncRun, sync_id)
        if run is None:
            return
        lock = _lock_for(run.connection_id)
        if lock.locked():
            _fail_run(db, run, ErrorCode.SYNC_ALREADY_RUNNING, "Concurrent sync blocked")
            return
        async with lock:
            await execute_sync(db, sync_id)
    finally:
        db.close()


async def execute_sync(db: Session, sync_id: int) -> SyncRun:
    run = db.get(SyncRun, sync_id)
    if run is None:
        raise not_found(ErrorCode.SYNC_NOT_FOUND, "Sync run not found")

    conn = db.get(BrokerConnection, run.connection_id)
    if conn is None:
        _fail_run(db, run, ErrorCode.CONNECTION_NOT_FOUND, "Connection missing")
        return run

    provider = db.get(Provider, conn.provider_id)
    if provider is None or not provider.enabled:
        _fail_run(db, run, ErrorCode.PROVIDER_UNAVAILABLE, "Provider unavailable")
        return run

    run.status = "running"
    run.started_at = utcnow()
    run.current_step = "decrypt_credentials"
    run.progress = Decimal("5")
    conn.status = "syncing"
    db.commit()

    connector = create_connector(provider.provider_key)
    credentials: dict[str, Any] = {}
    try:
        credentials = decrypt_credentials(db, conn)
        options = decrypt_options(db, conn)
        # Consume one-time OTP if previously submitted via /mfa
        pending_otp = options.pop("pending_otp", None)
        if pending_otp:
            credentials["otp"] = pending_otp

        run.current_step = "authenticate"
        run.progress = Decimal("15")
        db.commit()
        conn.status = "authenticating"
        db.commit()

        auth = await connector.authenticate(credentials, options)
        # Clear plaintext ASAP
        credentials.clear()
        # Persist broker browser session (e.g. storage_state) and drop one-shot OTP
        if auth.options_patch or pending_otp is not None:
            apply_options_patch(
                db,
                conn,
                auth.options_patch,
                drop_keys=["pending_otp"] if pending_otp is not None else None,
            )

        if auth.status == "mfa_required":
            run.status = "waiting_for_user"
            run.current_step = "mfa_required"
            run.error_code = ErrorCode.MFA_REQUIRED.value
            run.safe_error_message = auth.message or "MFA required"
            conn.status = "mfa_required"
            db.commit()
            return run

        if auth.status == "captcha_required":
            run.status = "waiting_for_user"
            run.current_step = "captcha_required"
            run.error_code = ErrorCode.CAPTCHA_REQUIRED.value
            run.safe_error_message = auth.message or "CAPTCHA required"
            conn.status = "waiting_for_user"
            db.commit()
            return run

        if not auth.success:
            _fail_run(
                db,
                run,
                ErrorCode.INVALID_CREDENTIALS,
                auth.message or "Authentication failed",
            )
            conn.status = "authentication_failed"
            conn.last_error_code = ErrorCode.INVALID_CREDENTIALS.value
            conn.last_sync_at = utcnow()
            db.commit()
            return run

        conn.status = "authenticated"
        run.current_step = "get_accounts"
        run.progress = Decimal("30")
        db.commit()

        accounts = await connector.get_accounts()
        run.accounts_found = len(accounts)
        run.progress = Decimal("40")
        db.commit()

        total_holdings = 0
        account_results: list[tuple[BrokerAccount, list[NormalizedHolding], Decimal | None]] = []

        for idx, n_acct in enumerate(accounts):
            run.current_step = f"get_holdings:{n_acct.external_account_id}"
            run.progress = Decimal("40") + Decimal(40) * Decimal(idx + 1) / Decimal(max(len(accounts), 1))
            db.commit()

            db_acct = _upsert_account(db, conn, n_acct)
            try:
                holdings = await connector.get_holdings(n_acct)
            except AppError as exc:
                if exc.code == ErrorCode.PORTFOLIO_EXTRACTION_FAILED:
                    # Preserve last known holdings — mark as partial failure
                    _add_sync_error(db, run, exc.code, exc.message)
                    holdings = []
                    # Mark existing holdings stale rather than deleting
                    _mark_holdings_stale(db, db_acct)
                    run.status = "completed_with_warnings"
                else:
                    raise

            cash = await connector.get_cash_balance(n_acct)
            if cash is not None:
                db_acct.cash_balance = cash
            db_acct.last_seen_at = utcnow()

            for n_holding in holdings:
                _upsert_holding(db, db_acct, n_holding, run.id)
                total_holdings += 1

            # Transactions only when reliably available
            try:
                txns = await connector.get_transactions(n_acct)
                for txn in txns:
                    _upsert_transaction(db, db_acct, txn)
            except Exception:
                logger.exception("Transaction import skipped for account %s", db_acct.id)

            account_results.append((db_acct, holdings, cash if isinstance(cash, Decimal) else db_acct.cash_balance))
            db.commit()

        run.current_step = "persist_snapshots"
        run.progress = Decimal("90")
        run.holdings_found = total_holdings
        run.records_found = total_holdings + len(accounts)
        db.commit()

        # Create snapshots for successful account extractions
        for db_acct, holdings, cash in account_results:
            if holdings or cash is not None:
                _create_account_snapshots(db, conn, db_acct, holdings, cash, run.id)

        if run.status != "completed_with_warnings":
            run.status = "completed"
        run.current_step = "done"
        run.progress = Decimal("100")
        run.completed_at = utcnow()
        conn.status = "connected"
        conn.last_sync_at = utcnow()
        conn.last_successful_sync_at = utcnow()
        conn.last_error_code = None
        db.commit()

        write_audit(
            db,
            user_id=conn.user_id,
            action="sync.completed",
            resource_type="sync_run",
            resource_id=run.id,
            metadata={
                "accounts": run.accounts_found,
                "holdings": run.holdings_found,
                "status": run.status,
            },
        )
        db.commit()
        db.refresh(run)
        return run

    except AppError as exc:
        logger.warning("Sync %s app error: %s", sync_id, exc.code)
        _fail_run(db, run, exc.code, exc.message)
        conn.status = "temporarily_unavailable"
        conn.last_error_code = exc.code.value
        conn.last_sync_at = utcnow()
        db.commit()
        return run
    except Exception:
        logger.exception("Sync %s failed", sync_id)
        _fail_run(db, run, ErrorCode.INTERNAL_ERROR, "Synchronization failed due to an internal error")
        conn.status = "temporarily_unavailable"
        conn.last_error_code = ErrorCode.INTERNAL_ERROR.value
        conn.last_sync_at = utcnow()
        db.commit()
        return run
    finally:
        credentials.clear()
        try:
            await connector.disconnect()
        except Exception:
            logger.exception("Connector disconnect failed after sync %s", sync_id)


def _fail_run(db: Session, run: SyncRun, code: ErrorCode, message: str) -> None:
    run.status = "failed"
    run.error_code = code.value
    run.safe_error_message = message
    run.completed_at = utcnow()
    run.current_step = "failed"
    _add_sync_error(db, run, code, message)
    db.commit()


def _add_sync_error(db: Session, run: SyncRun, code: ErrorCode, message: str) -> None:
    db.add(
        SyncError(
            sync_run_id=run.id,
            error_code=code.value,
            safe_message=message,
        )
    )
    db.flush()


def _upsert_account(db: Session, conn: BrokerConnection, n_acct: NormalizedAccount) -> BrokerAccount:
    acct = (
        db.query(BrokerAccount)
        .filter(
            BrokerAccount.connection_id == conn.id,
            BrokerAccount.external_account_id == n_acct.external_account_id,
        )
        .first()
    )
    if acct is None:
        acct = BrokerAccount(
            connection_id=conn.id,
            external_account_id=n_acct.external_account_id,
            account_name=n_acct.account_name,
            account_type=n_acct.account_type,
            currency=n_acct.currency,
            cash_balance=n_acct.cash_balance,
            last_seen_at=utcnow(),
        )
        db.add(acct)
        db.flush()
    else:
        acct.account_name = n_acct.account_name
        acct.account_type = n_acct.account_type
        if n_acct.currency is not None:
            acct.currency = n_acct.currency
        if n_acct.cash_balance is not None:
            acct.cash_balance = n_acct.cash_balance
        acct.last_seen_at = utcnow()
    return acct


def _upsert_holding(
    db: Session,
    account: BrokerAccount,
    n_holding: NormalizedHolding,
    sync_id: int,
) -> Holding:
    sec_id = n_holding.resolve_security_identifier()
    holding = (
        db.query(Holding)
        .filter(
            Holding.account_id == account.id,
            Holding.security_identifier == sec_id,
        )
        .first()
    )
    fields = {
        "symbol": n_holding.symbol,
        "security_name": n_holding.security_name,
        "isin": n_holding.isin,
        "asset_type": n_holding.asset_type,
        "quantity": n_holding.quantity,
        "average_cost": n_holding.average_cost,
        "cost_basis": n_holding.cost_basis,
        "current_price": n_holding.current_price,
        "market_value": n_holding.market_value,
        "unrealized_pnl": n_holding.unrealized_pnl,
        "currency": n_holding.currency,
        "valuation_timestamp": n_holding.valuation_timestamp,
        "last_seen_sync_id": sync_id,
        "observation_status": "observed",
    }
    if holding is None:
        holding = Holding(account_id=account.id, security_identifier=sec_id, **fields)
        db.add(holding)
    else:
        for key, value in fields.items():
            setattr(holding, key, value)
    db.flush()
    return holding


def _mark_holdings_stale(db: Session, account: BrokerAccount) -> None:
    db.query(Holding).filter(Holding.account_id == account.id).update(
        {"observation_status": "stale"},
        synchronize_session=False,
    )


def _upsert_transaction(db: Session, account: BrokerAccount, txn: Any) -> None:
    existing = (
        db.query(Transaction)
        .filter(
            Transaction.account_id == account.id,
            Transaction.external_transaction_id == txn.external_transaction_id,
        )
        .first()
    )
    if existing:
        return
    db.add(
        Transaction(
            account_id=account.id,
            external_transaction_id=txn.external_transaction_id,
            transaction_type=txn.transaction_type,
            symbol=txn.symbol,
            quantity=txn.quantity,
            unit_price=txn.unit_price,
            fees=txn.fees,
            total_amount=txn.total_amount,
            currency=txn.currency,
            transaction_date=txn.transaction_date,
        )
    )


def _create_account_snapshots(
    db: Session,
    conn: BrokerConnection,
    account: BrokerAccount,
    holdings: list[NormalizedHolding],
    cash: Decimal | None,
    sync_id: int,
) -> None:
    """Create per-currency snapshots. Avoid duplicate identical snapshots within same sync."""
    by_ccy: dict[str, dict[str, Decimal | None]] = {}
    for h in holdings:
        ccy = h.currency or account.currency or "UNKNOWN"
        bucket = by_ccy.setdefault(
            ccy,
            {
                "market": Decimal("0"),
                "cost": Decimal("0"),
                "pnl": Decimal("0"),
                "has_market": False,
                "has_cost": False,
                "has_pnl": False,
            },
        )
        if h.market_value is not None:
            bucket["market"] = (bucket["market"] or Decimal("0")) + h.market_value
            bucket["has_market"] = True
        if h.cost_basis is not None:
            bucket["cost"] = (bucket["cost"] or Decimal("0")) + h.cost_basis
            bucket["has_cost"] = True
        if h.unrealized_pnl is not None:
            bucket["pnl"] = (bucket["pnl"] or Decimal("0")) + h.unrealized_pnl
            bucket["has_pnl"] = True

    if not by_ccy and cash is not None:
        ccy = account.currency or "UNKNOWN"
        by_ccy[ccy] = {
            "market": None,
            "cost": None,
            "pnl": None,
            "has_market": False,
            "has_cost": False,
            "has_pnl": False,
        }

    now = utcnow()
    for ccy, vals in by_ccy.items():
        # Skip if identical snapshot already exists for this sync+account+currency
        existing = (
            db.query(PortfolioSnapshot)
            .filter(
                PortfolioSnapshot.sync_run_id == sync_id,
                PortfolioSnapshot.account_id == account.id,
                PortfolioSnapshot.currency == ccy,
            )
            .first()
        )
        if existing:
            continue
        db.add(
            PortfolioSnapshot(
                user_id=conn.user_id,
                connection_id=conn.id,
                account_id=account.id,
                sync_run_id=sync_id,
                total_market_value=vals["market"] if vals["has_market"] else None,
                total_cost_basis=vals["cost"] if vals["has_cost"] else None,
                cash_value=cash if (account.currency == ccy or account.currency is None) else None,
                unrealized_pnl=vals["pnl"] if vals["has_pnl"] else None,
                currency=ccy,
                valuation_timestamp=now,
                valuation_source="calculated",
                captured_at=now,
            )
        )
    db.flush()
