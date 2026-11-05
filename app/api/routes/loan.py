"""Loan collateral and eligibility endpoints (DB-backed)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.loan import (
    CollateralResponse,
    EligibilityResponse,
    SelectedEligibilityRequest,
    SelectedEligibilityResponse,
)
from app.services import loan_service
from app.services.audit_service import write_audit

router = APIRouter(prefix="/loan", tags=["loan"])


@router.get("/collateral", response_model=CollateralResponse)
def get_collateral(db: DbSession, user: CurrentUser) -> CollateralResponse:
    """All saved assets across connected brokers, values, and grand totals."""
    result = loan_service.list_collateral(db, user)
    write_audit(
        db,
        user_id=user.id,
        action="loan.collateral",
        resource_type="user",
        resource_id=user.id,
        metadata={"asset_count": len(result.assets)},
    )
    db.commit()
    return result


@router.get("/eligibility", response_model=EligibilityResponse)
def get_eligibility(db: DbSession, user: CurrentUser) -> EligibilityResponse:
    """Eligible loan based on grand total of all saved asset values (LTV × total)."""
    result = loan_service.eligibility_from_all_assets(db, user)
    write_audit(
        db,
        user_id=user.id,
        action="loan.eligibility_all",
        resource_type="user",
        resource_id=user.id,
        metadata={
            "eligible_loan": str(result.eligible_loan) if result.eligible_loan is not None else None,
            "asset_count": result.asset_count,
        },
    )
    db.commit()
    return result


@router.post("/eligibility/selected", response_model=SelectedEligibilityResponse)
def post_eligibility_selected(
    body: SelectedEligibilityRequest,
    db: DbSession,
    user: CurrentUser,
) -> SelectedEligibilityResponse:
    """Eligible loan based on user-selected holdings / cash accounts (LTV × selected)."""
    result = loan_service.eligibility_from_selected(db, user, body)
    write_audit(
        db,
        user_id=user.id,
        action="loan.eligibility_selected",
        resource_type="user",
        resource_id=user.id,
        metadata={
            "eligible_loan": str(result.eligible_loan) if result.eligible_loan is not None else None,
            "selected_count": len(result.selected_assets),
            "missing_count": len(result.missing_asset_ids),
        },
    )
    db.commit()
    return result
