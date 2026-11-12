"""ICON Stock Brokers (ICON Trade) — NaYa OMS at etrade.iconstockbrokersltd.com.

Implementation status: OPERATIONAL (live-verified 2026-11-12)
-------------------------------------------------------------
Same NaYa OMS stack as Fundvine. Portal brand ``ICON Trade``; ClientId and
CSCS observed via ``scripts/discover_iconstock_login.py``.
"""

from __future__ import annotations

from app.connectors.models import ImplementationStatus
from app.connectors.naya_oms.base import NaYaOMSConnector
from app.connectors.registry import register_connector


@register_connector("iconstock")
class IconStockConnector(NaYaOMSConnector):
    provider_id = "iconstock"
    provider_name = "ICON Stock Brokers"
    origin = "https://etrade.iconstockbrokersltd.com"
    approved_domains = frozenset({"etrade.iconstockbrokersltd.com"})
    portal_label = "etrade.iconstockbrokersltd.com"
    implementation_status = ImplementationStatus.OPERATIONAL
