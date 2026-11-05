"""Aggregate API router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    auth,
    connections,
    deposits,
    loan,
    markets,
    orders,
    portfolio,
    providers,
    schedules,
    syncs,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(providers.router)
api_router.include_router(connections.router)
api_router.include_router(syncs.router)
api_router.include_router(portfolio.router)
api_router.include_router(loan.router)
api_router.include_router(schedules.router)
api_router.include_router(deposits.router)
api_router.include_router(orders.router)
api_router.include_router(markets.router)
