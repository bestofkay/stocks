"""Connection credential schema validation."""

from __future__ import annotations

import pytest

from app.connectors.models import CredentialField
from app.core.errors import AppError
from app.services.connection_service import validate_credentials_against_schema


def test_required_fields_enforced():
    required = [
        CredentialField(name="username", label="Email", field_type="email", required=True),
        CredentialField(name="password", label="Password", field_type="password", required=True, secret=True),
    ]
    with pytest.raises(AppError) as exc:
        validate_credentials_against_schema({}, required, [])
    assert exc.value.status_code == 422


def test_email_alias_for_username():
    required = [
        CredentialField(name="username", label="Email", field_type="email", required=True),
        CredentialField(name="password", label="Password", field_type="password", required=True, secret=True),
    ]
    validate_credentials_against_schema(
        {"email": "a@b.com", "password": "x"},
        required,
        [],
    )


def test_unknown_fields_rejected():
    required = [
        CredentialField(name="username", label="Email", required=True),
        CredentialField(name="password", label="Password", required=True, secret=True),
    ]
    with pytest.raises(AppError):
        validate_credentials_against_schema(
            {"username": "a", "password": "b", "extra": "nope"},
            required,
            [],
        )
