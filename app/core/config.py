"""Application configuration loaded from environment variables."""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime settings. Required secrets fail fast at startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "staging", "production", "test"] = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    app_name: str = "Portfolio Aggregator"

    database_url: str = Field(
        ...,
        description="SQLAlchemy MySQL URL, e.g. mysql+pymysql://user:pass@host:3306/db",
    )

    app_encryption_key: str = Field(
        ...,
        description="Base64-encoded 32-byte AES-256 key for credential encryption",
    )
    app_secret_key: str = Field(
        ...,
        min_length=32,
        description="Secret used to sign application sessions",
    )

    cors_origins: str = ""

    playwright_headless: bool = True
    playwright_timeout_ms: int = 30_000

    log_level: str = "INFO"

    scheduler_enabled: bool = True
    scheduler_lock_key: str = "portfolio_scheduler_lock"

    session_cookie_name: str = "portfolio_session"
    session_max_age_seconds: int = 86_400
    refresh_token_max_age_seconds: int = 604_800
    auth_rate_limit: str = "10/minute"

    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None

    max_request_body_bytes: int = 1_048_576

    # Loan eligibility: eligible_loan = loan_ltv_ratio × collateral asset value
    loan_ltv_ratio: str = "0.30"

    # Encryption key version embedded in ciphertext envelopes
    encryption_key_version: int = 1

    @field_validator("app_encryption_key")
    @classmethod
    def validate_encryption_key(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError(
                "APP_ENCRYPTION_KEY is required. "
                "Generate one with: python scripts/generate_keys.py"
            )
        try:
            raw = base64.b64decode(value, validate=True)
        except binascii.Error as exc:
            raise ValueError(
                "APP_ENCRYPTION_KEY must be valid base64 encoding a 32-byte key"
            ) from exc
        if len(raw) != 32:
            raise ValueError(
                f"APP_ENCRYPTION_KEY must decode to exactly 32 bytes, got {len(raw)}"
            )
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith("mysql"):
            raise ValueError("DATABASE_URL must use a MySQL dialect (mysql+pymysql://...)")
        return value

    @model_validator(mode="after")
    def validate_production_https_hint(self) -> Settings:
        if self.app_env == "production" and self.app_host in {"0.0.0.0", "127.0.0.1"}:
            # Host binding alone is fine behind a reverse proxy; no hard failure.
            pass
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        if not self.cors_origins.strip():
            return []
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def encryption_key_bytes(self) -> bytes:
        return base64.b64decode(self.app_encryption_key)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cookie_secure(self) -> bool:
        return self.is_production


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def clear_settings_cache() -> None:
    get_settings.cache_clear()
