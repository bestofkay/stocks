"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app import __version__
from app.api.router import api_router
from app.connectors.registry import discover_connectors
from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import setup_logging
from app.core.rate_limit import limiter
from app.db.database import check_db_connection, init_db, session_scope
from app.services.auth_service import bootstrap_admin_if_configured
from app.services.provider_service import sync_providers_to_db
from app.services.scheduler_service import start_scheduler, stop_scheduler
from app.services.sync_service import recover_interrupted_syncs, shutdown_sync_tasks

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if get_settings().is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        max_bytes = get_settings().max_request_body_bytes
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > max_bytes:
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": ErrorCode.VALIDATION_ERROR.value,
                        "message": "Request body too large",
                    }
                },
            )
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info("Starting %s v%s (%s)", settings.app_name, __version__, settings.app_env)

    init_db()
    check_db_connection()
    discover_connectors()

    with session_scope() as db:
        sync_providers_to_db(db)
        bootstrap_admin_if_configured(db)
        recover_interrupted_syncs(db)

    await start_scheduler()
    logger.info("Application ready")
    try:
        yield
    finally:
        await shutdown_sync_tasks()
        await stop_scheduler()
        logger.info("Application shutdown complete")


@asynccontextmanager
async def _testing_lifespan(app: FastAPI):
    """No DB/scheduler side effects — used by unit/API tests."""
    discover_connectors()
    yield


def create_app(*, testing: bool = False) -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Backend-only multi-broker investment portfolio aggregation service. "
            "Manage connections, synchronize holdings, and query a unified portfolio via REST."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=_testing_lifespan if testing else lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        def _jsonable(value):  # noqa: ANN001
            if isinstance(value, BaseException):
                return str(value)
            if isinstance(value, dict):
                return {k: _jsonable(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [_jsonable(v) for v in value]
            return value

        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": ErrorCode.VALIDATION_ERROR.value,
                    "message": "Request validation failed",
                    "details": {"errors": _jsonable(exc.errors())},
                }
            },
        )

    @app.get("/health", tags=["system"])
    def health() -> dict:
        return {"status": "ok", "version": __version__, "env": settings.app_env}

    app.include_router(api_router)
    return app


# Production/runtime entrypoint. Tests should call create_app(testing=True).
app = create_app()
