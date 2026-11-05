"""Standardized API error codes and exceptions."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    PROVIDER_NOT_FOUND = "PROVIDER_NOT_FOUND"
    CONNECTOR_NOT_IMPLEMENTED = "CONNECTOR_NOT_IMPLEMENTED"
    CONNECTOR_NOT_OPERATIONAL = "CONNECTOR_NOT_OPERATIONAL"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    MFA_REQUIRED = "MFA_REQUIRED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    AUTHENTICATION_TIMEOUT = "AUTHENTICATION_TIMEOUT"
    PORTFOLIO_EXTRACTION_FAILED = "PORTFOLIO_EXTRACTION_FAILED"
    PARTIAL_DATA = "PARTIAL_DATA"
    DATABASE_ERROR = "DATABASE_ERROR"
    SYNC_ALREADY_RUNNING = "SYNC_ALREADY_RUNNING"
    CONNECTION_NOT_FOUND = "CONNECTION_NOT_FOUND"
    ACCOUNT_NOT_FOUND = "ACCOUNT_NOT_FOUND"
    ASSET_NOT_FOUND = "ASSET_NOT_FOUND"
    SYNC_NOT_FOUND = "SYNC_NOT_FOUND"
    SCHEDULE_NOT_FOUND = "SCHEDULE_NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    CONFLICT = "CONFLICT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    NAVIGATION_BLOCKED = "NAVIGATION_BLOCKED"
    BOOTSTRAP_REQUIRED = "BOOTSTRAP_REQUIRED"
    ALREADY_REGISTERED = "ALREADY_REGISTERED"


class AppError(Exception):
    """Application error with a safe client-facing message."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": {
                "code": self.code.value,
                "message": self.message,
            }
        }
        if self.details:
            payload["error"]["details"] = self.details
        return payload


def not_found(code: ErrorCode, message: str) -> AppError:
    return AppError(code, message, status_code=404)


def unauthorized(message: str = "Authentication required") -> AppError:
    return AppError(ErrorCode.UNAUTHORIZED, message, status_code=401)


def forbidden(message: str = "Access denied") -> AppError:
    return AppError(ErrorCode.FORBIDDEN, message, status_code=403)


def conflict(code: ErrorCode, message: str) -> AppError:
    return AppError(code, message, status_code=409)
