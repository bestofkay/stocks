"""Unit tests for loan collateral / eligibility (30% LTV, holdings only)."""

from __future__ import annotations

import base64
import os
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault(
    "APP_ENCRYPTION_KEY",
    base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
)
os.environ.setdefault("APP_SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault(
    "DATABASE_URL",
    "mysql+pymysql://portfolio_user:change_me@127.0.0.1:3306/portfolio_test",
)
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SCHEDULER_ENABLED", "false")

from app.api.deps import get_current_user, get_db  # noqa: E402
from app.core.config import clear_settings_cache  # noqa: E402
from app.core.errors import AppError, ErrorCode  # noqa: E402
from app.main import create_app  # noqa: E402
from app.schemas.loan import (  # noqa: E402
    CollateralAsset,
    CollateralResponse,
    CurrencyCollateralTotal,
    EligibilityResponse,
    SelectedEligibilityRequest,
    SelectedEligibilityResponse,
)
from app.services import loan_service  # noqa: E402


@pytest.fixture(autouse=True)
def _settings():
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture
def user():
    return SimpleNamespace(
        id=1,
        email="borrower@example.com",
        is_active=True,
        is_admin=False,
        created_at=datetime.now(timezone.utc),
    )


def _asset(**kwargs) -> CollateralAsset:
    defaults = dict(
        asset_id="holding:1",
        asset_kind="holding",
        holding_id=1,
        account_id=10,
        connection_id=100,
        provider_id="afrinvestor",
        provider_name="Afrinvestor",
        account_name="Main",
        symbol="MTNN",
        security_name="MTN Nigeria",
        quantity=Decimal("100"),
        currency="NGN",
        asset_value=Decimal("100000.00"),
        valuation_timestamp=None,
        observation_status="observed",
        last_synced_at=None,
    )
    defaults.update(kwargs)
    return CollateralAsset(**defaults)


def test_eligible_is_thirty_percent():
    assert loan_service._eligible(Decimal("100000")) == Decimal("30000.00")
    assert loan_service._eligible(Decimal("1")) == Decimal("0.30")


def test_parse_holding_asset_ids_rejects_cash():
    h, bad = loan_service._parse_holding_asset_ids(
        ["holding:12", "cash:3", "holding:12", "foo", "cash:x"]
    )
    assert h == {12}
    assert "cash:3" in bad
    assert "foo" in bad
    assert "cash:x" in bad


def test_currency_totals_holdings_only():
    assets = [
        _asset(asset_id="holding:1", holding_id=1, asset_value=Decimal("70000.00")),
        _asset(asset_id="holding:2", holding_id=2, asset_value=Decimal("30000.00")),
    ]
    totals = loan_service._currency_totals(assets, ratio=Decimal("0.30"))
    assert len(totals) == 1
    assert totals[0].total_asset_value == Decimal("100000.00")
    assert totals[0].eligible_loan == Decimal("30000.00")
    gv, gl = loan_service._single_currency_total(totals)
    assert gv == Decimal("100000.00")
    assert gl == Decimal("30000.00")


def test_multi_currency_grand_total_null():
    assets = [
        _asset(currency="NGN", asset_value=Decimal("100")),
        _asset(asset_id="holding:2", holding_id=2, currency="USD", asset_value=Decimal("50")),
    ]
    totals = loan_service._currency_totals(assets, ratio=Decimal("0.30"))
    assert len(totals) == 2
    assert loan_service._single_currency_total(totals) == (None, None)


def test_eligibility_from_selected_happy_path(user):
    assets = [
        _asset(asset_id="holding:1", holding_id=1, asset_value=Decimal("40000.00")),
        _asset(asset_id="holding:2", holding_id=2, asset_value=Decimal("60000.00")),
    ]
    db = MagicMock()
    with patch.object(loan_service, "_load_collateral_rows", return_value=assets):
        result = loan_service.eligibility_from_selected(
            db,
            user,
            SelectedEligibilityRequest(holding_ids=[1]),
        )
    assert result.total_asset_value == Decimal("40000.00")
    assert result.eligible_loan == Decimal("12000.00")
    assert {a.asset_id for a in result.selected_assets} == {"holding:1"}
    assert result.missing_asset_ids == []


def test_eligibility_from_selected_via_asset_ids(user):
    assets = [_asset(asset_id="holding:5", holding_id=5, asset_value=Decimal("1000.00"))]
    db = MagicMock()
    with patch.object(loan_service, "_load_collateral_rows", return_value=assets):
        result = loan_service.eligibility_from_selected(
            db,
            user,
            SelectedEligibilityRequest(asset_ids=["holding:5"]),
        )
    assert result.eligible_loan == Decimal("300.00")


def test_cash_asset_id_not_accepted(user):
    assets = [_asset(asset_id="holding:1", holding_id=1, asset_value=Decimal("1000.00"))]
    db = MagicMock()
    with patch.object(loan_service, "_load_collateral_rows", return_value=assets):
        with pytest.raises(AppError) as exc:
            loan_service.eligibility_from_selected(
                db,
                user,
                SelectedEligibilityRequest(asset_ids=["cash:9"]),
            )
    assert exc.value.code == ErrorCode.ASSET_NOT_FOUND


def test_eligibility_from_selected_all_missing(user):
    db = MagicMock()
    with patch.object(loan_service, "_load_collateral_rows", return_value=[]):
        with pytest.raises(AppError) as exc:
            loan_service.eligibility_from_selected(
                db,
                user,
                SelectedEligibilityRequest(holding_ids=[99]),
            )
    assert exc.value.code == ErrorCode.ASSET_NOT_FOUND


def test_eligibility_requires_selection(user):
    db = MagicMock()
    with pytest.raises(AppError) as exc:
        loan_service.eligibility_from_selected(db, user, SelectedEligibilityRequest())
    assert exc.value.code == ErrorCode.VALIDATION_ERROR


@pytest.fixture
def client(user):
    clear_settings_cache()
    app = create_app(testing=True)

    def _db():
        yield MagicMock()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def test_loan_routes(client):
    assets = [_asset(asset_value=Decimal("100000.00"))]
    by_ccy = [
        CurrencyCollateralTotal(
            currency="NGN",
            total_asset_value=Decimal("100000.00"),
            asset_count=1,
            eligible_loan=Decimal("30000.00"),
        )
    ]
    collateral = CollateralResponse(
        loan_to_value_ratio=Decimal("0.30"),
        assets=assets,
        by_provider=[],
        by_currency=by_ccy,
        grand_total_asset_value=Decimal("100000.00"),
        grand_total_eligible_loan=Decimal("30000.00"),
    )
    eligibility = EligibilityResponse(
        loan_to_value_ratio=Decimal("0.30"),
        by_currency=by_ccy,
        total_asset_value=Decimal("100000.00"),
        eligible_loan=Decimal("30000.00"),
        asset_count=1,
    )
    selected = SelectedEligibilityResponse(
        loan_to_value_ratio=Decimal("0.30"),
        by_currency=by_ccy,
        total_asset_value=Decimal("100000.00"),
        eligible_loan=Decimal("30000.00"),
        asset_count=1,
        selected_assets=assets,
        missing_asset_ids=[],
    )

    with (
        patch.object(loan_service, "list_collateral", return_value=collateral),
        patch.object(loan_service, "eligibility_from_all_assets", return_value=eligibility),
        patch.object(loan_service, "eligibility_from_selected", return_value=selected),
        patch("app.api.routes.loan.write_audit"),
    ):
        r1 = client.get("/api/v1/loan/collateral")
        assert r1.status_code == 200
        assert r1.json()["grand_total_eligible_loan"] == "30000.00"
        assert "cash_value" not in r1.json()["by_currency"][0]

        r2 = client.get("/api/v1/loan/eligibility")
        assert r2.status_code == 200
        assert r2.json()["eligible_loan"] == "30000.00"

        r3 = client.post(
            "/api/v1/loan/eligibility/selected",
            json={"holding_ids": [1]},
        )
        assert r3.status_code == 200
        assert r3.json()["eligible_loan"] == "30000.00"
