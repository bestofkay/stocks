"""Application authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request, Response

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.core.security import get_session_token_service
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.schemas.syncs import SyncAcceptedResponse
from app.services import auth_service, sync_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, access_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=access_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_max_age_seconds,
        path="/",
    )


@router.post("/register", response_model=UserResponse, status_code=201)
@limiter.limit("5/minute")
def register(request: Request, body: RegisterRequest, db: DbSession) -> UserResponse:
    user = auth_service.register_user(db, body.email, body.password)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    db: DbSession,
    background_tasks: BackgroundTasks,
) -> TokenResponse:
    settings = get_settings()
    user = auth_service.authenticate_user(db, body.email, body.password)
    access, refresh, _session = auth_service.create_session(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    # Refresh stored collateral assets for all connected brokers (upsert via sync)
    syncs_started: list[SyncAcceptedResponse] = []
    for run in sync_service.enqueue_idle_syncs_for_user(db, user):
        background_tasks.add_task(sync_service.start_sync_background, run.id)
        syncs_started.append(SyncAcceptedResponse.model_validate(sync_service.sync_accepted(run)))
    _set_session_cookie(response, access)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.session_max_age_seconds,
        syncs_started=syncs_started,
    )


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("20/minute")
def refresh(request: Request, response: Response, body: RefreshRequest, db: DbSession) -> TokenResponse:
    settings = get_settings()
    access, new_refresh, _user = auth_service.refresh_session(db, body.refresh_token)
    _set_session_cookie(response, access)
    return TokenResponse(
        access_token=access,
        refresh_token=new_refresh,
        expires_in=settings.session_max_age_seconds,
    )


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: DbSession, user: CurrentUser) -> None:
    settings = get_settings()
    session_id = None
    token = request.cookies.get(settings.session_cookie_name)
    auth = request.headers.get("authorization")
    if not token and auth and auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1]
    if token:
        try:
            payload = get_session_token_service().verify_access_token(token)
            session_id = payload.get("sid")
        except ValueError:
            pass
    auth_service.revoke_session(db, user, session_id)
    response.delete_cookie(settings.session_cookie_name, path="/")


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)
