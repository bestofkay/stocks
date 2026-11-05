"""Structured application logging with secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

from app.core.encryption import SECRET_FIELD_NAMES, redact_secrets

# Patterns that may appear in free-form log messages
_SENSITIVE_PATTERNS = [
    re.compile(r"(password\s*[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"(api[_-]?secret\s*[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"(authorization\s*[=:]\s*bearer\s+)\S+", re.IGNORECASE),
    re.compile(r"(token\s*[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"(cookie\s*[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"(otp\s*[=:]\s*)\S+", re.IGNORECASE),
]


class RedactingFilter(logging.Filter):
    """Strip secrets from log records before they are emitted."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._redact_string(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = redact_secrets(record.args)
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_secrets(a) if isinstance(a, (dict, list)) else self._redact_value(a)
                    for a in record.args
                )
        return True

    def _redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact_string(value)
        return value

    def _redact_string(self, text: str) -> str:
        result = text
        for pattern in _SENSITIVE_PATTERNS:
            result = pattern.sub(r"\1[REDACTED]", result)
        for name in SECRET_FIELD_NAMES:
            # JSON-ish "password": "..."
            result = re.sub(
                rf'("{name}"\s*:\s*")[^"]*(")',
                r"\1[REDACTED]\2",
                result,
                flags=re.IGNORECASE,
            )
        return result


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)

    # Quiet noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
