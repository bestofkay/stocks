"""WSTC Securities connector — Zanibal E-Business Suite at customerportal.wstc.com.ng/iwnewebiz.

Implementation status: SCAFFOLDED. Pages, tables and forms observed in an
authorized session (2026-11-02). The login page AES-encrypts the password
client-side with a key embedded in the page (CryptoJS, AES-128-CBC, key = IV);
we reproduce that. Flip to OPERATIONAL after scripts/record_iw_flow.py passes.
"""

from __future__ import annotations

from app.connectors.registry import register_connector
from app.connectors.zanibal_iw.base import ZanibalIWConnector


@register_connector("wstc")
class WSTCConnector(ZanibalIWConnector):
    provider_id = "wstc"
    provider_name = "WSTC Securities"
    origin = "https://customerportal.wstc.com.ng"
    base_path = "/iwnewebiz"
    approved_domains = frozenset({"customerportal.wstc.com.ng"})
    # Public constant from the login page script (not a secret): CryptoJS key and IV
    password_aes_key = "5185536683906693"
    username_label = "User ID (customer number)"
