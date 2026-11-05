"""Application authentication: Argon2id passwords and signed sessions."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import Settings, get_settings

# Argon2id is the default type for PasswordHasher
_ph = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _ph.check_needs_rehash(password_hash)
    except Exception:
        return True


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Store only a hash of session/refresh tokens in the database."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SessionTokenService:
    """Create and verify signed, time-limited session tokens."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._serializer = URLSafeTimedSerializer(
            secret_key=self._settings.app_secret_key,
            salt="portfolio-session-v1",
        )

    def create_access_token(self, user_id: int, session_id: str) -> str:
        payload = {"uid": user_id, "sid": session_id, "typ": "access", "jti": generate_token(8)}
        return self._serializer.dumps(payload)

    def create_refresh_token(self, user_id: int, session_id: str) -> str:
        # jti ensures each issued refresh token is unique (enables rotation detection)
        payload = {
            "uid": user_id,
            "sid": session_id,
            "typ": "refresh",
            "jti": generate_token(12),
        }
        return self._serializer.dumps(payload)

    def verify_access_token(self, token: str) -> dict[str, Any]:
        return self._loads(token, max_age=self._settings.session_max_age_seconds, expected_type="access")

    def verify_refresh_token(self, token: str) -> dict[str, Any]:
        return self._loads(
            token,
            max_age=self._settings.refresh_token_max_age_seconds,
            expected_type="refresh",
        )

    def _loads(self, token: str, *, max_age: int, expected_type: str) -> dict[str, Any]:
        try:
            data = self._serializer.loads(token, max_age=max_age)
        except SignatureExpired as exc:
            raise ValueError("Token expired") from exc
        except BadSignature as exc:
            raise ValueError("Invalid token") from exc
        if not isinstance(data, dict) or data.get("typ") != expected_type:
            raise ValueError("Invalid token type")
        if "uid" not in data or "sid" not in data:
            raise ValueError("Invalid token payload")
        return data


def get_session_token_service() -> SessionTokenService:
    return SessionTokenService()
