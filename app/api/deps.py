"""FastAPI dependencies for auth and database."""

from __future__ import annotations

from datetime import timezone
from typing import Annotated

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import forbidden, unauthorized
from app.core.security import get_session_token_service, utcnow
from app.db.database import get_db
from app.db.models import User, UserSession

DbSession = Annotated[Session, Depends(get_db)]


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _ensure_aware(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def get_current_user(
    request: Request,
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    settings = get_settings()
    token = _extract_bearer(authorization)
    if not token:
        token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise unauthorized()

    token_svc = get_session_token_service()
    try:
        payload = token_svc.verify_access_token(token)
    except ValueError as exc:
        raise unauthorized(str(exc)) from exc

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

    if _ensure_aware(session.expires_at) < utcnow():
        raise unauthorized("Session expired")

    user = db.get(User, payload["uid"])
    if user is None or not user.is_active:
        raise unauthorized("User inactive or not found")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_resource_owner(user: User, owner_id: int) -> None:
    if user.id != owner_id and not user.is_admin:
        raise forbidden("You do not own this resource")
