"""Fundvine Capital & Securities — NaYa OMS at etrade.fundvinesecurities.com.

Implementation status: OPERATIONAL (live-verified 2026-11-12)
"""

from __future__ import annotations

from app.connectors.models import ImplementationStatus
from app.connectors.naya_oms.base import NaYaOMSConnector
from app.connectors.registry import register_connector


@register_connector("fundvine")
class FundvineConnector(NaYaOMSConnector):
    provider_id = "fundvine"
    provider_name = "Fundvine Capital & Securities"
    origin = "https://etrade.fundvinesecurities.com"
    approved_domains = frozenset(
        {
            "etrade.fundvinesecurities.com",
            "offer.fundvinesecurities.com",
        }
    )
    portal_label = "etrade.fundvinesecurities.com"
    implementation_status = ImplementationStatus.OPERATIONAL
