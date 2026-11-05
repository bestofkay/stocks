"""AES-256-GCM credential encryption with key versioning."""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Envelope format version (serialization), distinct from key version
ENVELOPE_FORMAT = 1
NONCE_SIZE = 12


class EncryptionError(Exception):
    """Raised when encryption or decryption fails."""


@dataclass(frozen=True)
class EncryptedEnvelope:
    """Versioned ciphertext envelope stored in the database."""

    format: int
    key_version: int
    nonce_b64: str
    ciphertext_b64: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "f": self.format,
                "kv": self.key_version,
                "n": self.nonce_b64,
                "c": self.ciphertext_b64,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> EncryptedEnvelope:
        try:
            data = json.loads(raw)
            return cls(
                format=int(data["f"]),
                key_version=int(data["kv"]),
                nonce_b64=str(data["n"]),
                ciphertext_b64=str(data["c"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EncryptionError("Invalid encrypted envelope") from exc


class EncryptionService:
    """Encrypts and decrypts broker secrets using AES-256-GCM.

    Key rotation:
      1. Generate a new 32-byte key and set APP_ENCRYPTION_KEY / bump ENCRYPTION_KEY_VERSION.
      2. Keep the previous key available via APP_ENCRYPTION_KEY_PREVIOUS (optional future).
      3. Run `python scripts/rotate_encryption_keys.py` to re-encrypt all stored envelopes.
      4. Retire the previous key after verification.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._aesgcm = AESGCM(self._settings.encryption_key_bytes)
        self._key_version = self._settings.encryption_key_version

    @property
    def key_version(self) -> int:
        return self._key_version

    def encrypt_bytes(self, plaintext: bytes, *, aad: bytes | None = None) -> str:
        """Encrypt raw bytes; returns a JSON envelope string."""
        if not plaintext:
            raise EncryptionError("Cannot encrypt empty plaintext")
        nonce = os.urandom(NONCE_SIZE)
        ciphertext = self._aesgcm.encrypt(nonce, plaintext, aad)
        envelope = EncryptedEnvelope(
            format=ENVELOPE_FORMAT,
            key_version=self._key_version,
            nonce_b64=base64.b64encode(nonce).decode("ascii"),
            ciphertext_b64=base64.b64encode(ciphertext).decode("ascii"),
        )
        return envelope.to_json()

    def decrypt_bytes(self, envelope_json: str, *, aad: bytes | None = None) -> bytes:
        envelope = EncryptedEnvelope.from_json(envelope_json)
        if envelope.format != ENVELOPE_FORMAT:
            raise EncryptionError(f"Unsupported envelope format: {envelope.format}")
        if envelope.key_version != self._key_version:
            raise EncryptionError(
                f"Key version mismatch: envelope={envelope.key_version}, "
                f"current={self._key_version}. Run key rotation."
            )
        try:
            nonce = base64.b64decode(envelope.nonce_b64)
            ciphertext = base64.b64decode(envelope.ciphertext_b64)
            return self._aesgcm.decrypt(nonce, ciphertext, aad)
        except Exception as exc:
            raise EncryptionError("Decryption failed") from exc

    def encrypt_dict(self, data: dict[str, Any], *, aad: bytes | None = None) -> str:
        payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return self.encrypt_bytes(payload, aad=aad)

    def decrypt_dict(self, envelope_json: str, *, aad: bytes | None = None) -> dict[str, Any]:
        raw = self.decrypt_bytes(envelope_json, aad=aad)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EncryptionError("Decrypted payload is not valid JSON") from exc
        if not isinstance(data, dict):
            raise EncryptionError("Decrypted payload must be a JSON object")
        return data

    def encrypt_secret_field(self, value: str, *, field_name: str) -> str:
        """Encrypt a single secret field with field-name AAD binding."""
        aad = f"field:{field_name}".encode("utf-8")
        return self.encrypt_bytes(value.encode("utf-8"), aad=aad)

    def decrypt_secret_field(self, envelope_json: str, *, field_name: str) -> str:
        aad = f"field:{field_name}".encode("utf-8")
        return self.decrypt_bytes(envelope_json, aad=aad).decode("utf-8")


# Sensitive keys never logged or returned in API responses
SECRET_FIELD_NAMES = frozenset(
    {
        "password",
        "passphrase",
        "api_key",
        "api_secret",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "session_token",
        "cookie",
        "cookies",
        "otp",
        "mfa_code",
        "pin",
        "credentials",
        "authorization",
    }
)


def redact_secrets(data: Any, *, placeholder: str = "[REDACTED]") -> Any:
    """Recursively redact known secret fields from nested structures."""
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if str(key).lower() in SECRET_FIELD_NAMES:
                result[key] = placeholder
            else:
                result[key] = redact_secrets(value, placeholder=placeholder)
        return result
    if isinstance(data, list):
        return [redact_secrets(item, placeholder=placeholder) for item in data]
    return data


def get_encryption_service() -> EncryptionService:
    return EncryptionService()
