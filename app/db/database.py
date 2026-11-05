"""SQLAlchemy engine and session management."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _create_engine():
    settings = get_settings()
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=3600,
        echo=False,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def set_utc(dbapi_connection, connection_record):  # noqa: ARG001
        cursor = dbapi_connection.cursor()
        cursor.execute("SET time_zone = '+00:00'")
        cursor.close()

    return engine


engine = None
SessionLocal = None


def init_db() -> None:
    """Initialize engine and session factory (call after settings are loadable)."""
    global engine, SessionLocal
    engine = _create_engine()
    SessionLocal = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )


def get_engine():
    if engine is None:
        init_db()
    return engine


def get_session_factory():
    if SessionLocal is None:
        init_db()
    return SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session."""
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Context manager for scripts and background jobs."""
    factory = get_session_factory()
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_db_connection() -> bool:
    """Return True if the database accepts connections."""
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return True
