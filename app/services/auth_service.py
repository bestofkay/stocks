"""Application user authentication service."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode, conflict, unauthorized
from app.core.security import (
    generate_token,
    get_session_token_service,
    hash_password,
    hash_token,
    needs_rehash,
    utcnow,
    verify_password,
)
from app.db.models import User, UserSession
from app.services.audit_service import write_audit


def register_user(db: Session, email: str, password: str) -> User:
    existing = db.query(User).filter(User.email == email.lower()).first()
    if existing:
        raise conflict(ErrorCode.ALREADY_REGISTERED, "An account with this email already exists")

    user_count = db.query(User).count()
    user = User(
        email=email.lower().strip(),
        password_hash=hash_password(password),
        is_active=True,
        is_admin=user_count == 0,  # first user becomes admin
    )
    db.add(user)
    db.flush()
    write_audit(db, user_id=user.id, action="user.register", resource_type="user", resource_id=user.id)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User:
    user = db.query(User).filter(User.email == email.lower()).first()
    if user is None or not verify_password(user.password_hash, password):
        raise unauthorized("Invalid email or password")
    if not user.is_active:
        raise unauthorized("Account is disabled")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        db.add(user)
        db.commit()
    return user


def create_session(
    db: Session,
    user: User,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[str, str, UserSession]:
    settings = get_settings()
    token_svc = get_session_token_service()
    session_id = generate_token(24)
    session = UserSession(
        user_id=user.id,
        session_id=session_id,
        refresh_token_hash="pending",
        expires_at=utcnow() + timedelta(seconds=settings.refresh_token_max_age_seconds),
        user_agent=(user_agent or "")[:512] or None,
        ip_address=(ip_address or "")[:64] or None,
    )
    db.add(session)
    db.flush()
    access = token_svc.create_access_token(user.id, session_id)
    refresh = token_svc.create_refresh_token(user.id, session_id)
    session.refresh_token_hash = hash_token(refresh)
    write_audit(db, user_id=user.id, action="user.login", resource_type="session", resource_id=session.id)
    db.commit()
    db.refresh(session)
    return access, refresh, session


def revoke_session(db: Session, user: User, session_id: str | None = None) -> None:
    q = db.query(UserSession).filter(
        UserSession.user_id == user.id,
        UserSession.revoked_at.is_(None),
    )
    if session_id:
        q = q.filter(UserSession.session_id == session_id)
    now = utcnow()
    for sess in q.all():
        sess.revoked_at = now
    write_audit(db, user_id=user.id, action="user.logout", resource_type="session", resource_id=session_id)
    db.commit()


def refresh_session(db: Session, refresh_token: str) -> tuple[str, str, User]:
    """Exchange a valid refresh token for a new access + refresh token pair.

    Rotates the stored refresh token hash (revocable sessions).
    """
    settings = get_settings()
    token_svc = get_session_token_service()
    try:
        payload = token_svc.verify_refresh_token(refresh_token)
    except ValueError as exc:
        raise unauthorized("Invalid or expired refresh token") from exc

    session = (
        db.query(UserSession)
        .filter(
            UserSession.session_id == payload["sid"],
            UserSession.user_id == payload["uid"],
            UserSession.revoked_at.is_(None),
        )
        .first()
    )
    if session is None:
        raise unauthorized("Session revoked or unknown")

    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        from datetime import timezone

        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < utcnow():
        raise unauthorized("Session expired")

    if session.refresh_token_hash != hash_token(refresh_token):
        # Possible reuse / theft — revoke session
        session.revoked_at = utcnow()
        db.commit()
        raise unauthorized("Refresh token reuse detected; session revoked")

    user = db.get(User, payload["uid"])
    if user is None or not user.is_active:
        raise unauthorized("User inactive or not found")

    access = token_svc.create_access_token(user.id, session.session_id)
    new_refresh = token_svc.create_refresh_token(user.id, session.session_id)
    session.refresh_token_hash = hash_token(new_refresh)
    session.expires_at = utcnow() + timedelta(seconds=settings.refresh_token_max_age_seconds)
    write_audit(
        db,
        user_id=user.id,
        action="user.refresh",
        resource_type="session",
        resource_id=session.id,
    )
    db.commit()
    return access, new_refresh, user


def bootstrap_admin_if_configured(db: Session) -> User | None:
    """Create admin from BOOTSTRAP_ADMIN_* env vars if no users exist."""
    settings = get_settings()
    if db.query(User).count() > 0:
        return None
    email = settings.bootstrap_admin_email
    password = settings.bootstrap_admin_password
    if not email or not password:
        return None
    if len(password) < 12:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters",
            status_code=500,
        )
    user = User(
        email=email.lower().strip(),
        password_hash=hash_password(password),
        is_active=True,
        is_admin=True,
    )
    db.add(user)
    db.flush()
    write_audit(
        db,
        user_id=user.id,
        action="user.bootstrap",
        resource_type="user",
        resource_id=user.id,
    )
    db.commit()
    db.refresh(user)
    return user
