"""Stanbic IBTC Stockbrokers (SISL) connector — Zanibal E-Business Suite at www.sislbrokerage.com/IW.

Implementation status: SCAFFOLDED. Same portal software as WSTC. The SISL
login page (inspected 2026-10-31) posts the password as typed and uses a
renamed anti-forgery field (``__RequestVerificationToken_<suffix>``), which the
shared base forwards automatically. Post-login pages are assumed to match the
WSTC deployment (not yet observed while signed in to SISL).
"""

from __future__ import annotations

from app.connectors.registry import register_connector
from app.connectors.zanibal_iw.base import ZanibalIWConnector


@register_connector("sisl")
class SISLConnector(ZanibalIWConnector):
    provider_id = "sisl"
    provider_name = "Stanbic IBTC Stockbrokers (SISL)"
    origin = "https://www.sislbrokerage.com"
    base_path = "/IW"
    approved_domains = frozenset({"www.sislbrokerage.com", "sislbrokerage.com"})
    password_aes_key = None
    username_label = "Username"
    extra_blockers = ["Post-login SISL pages not yet observed; assumed identical to the WSTC deployment of the same software."]
