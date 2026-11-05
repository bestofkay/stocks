"""Provider registry discovery tests."""

from __future__ import annotations

from app.connectors.models import ImplementationStatus
from app.connectors.registry import (
    discover_connectors,
    get_provider_metadata,
    list_provider_metadata,
)


def test_afrinvestor_registered():
    discover_connectors()
    providers = {p.provider_id: p for p in list_provider_metadata()}
    assert "afrinvestor" in providers
    assert "morgan" in providers
    assert "example_broker" in providers
    meta = providers["afrinvestor"]
    assert meta.provider_name == "Afrinvestor"
    assert meta.implementation_status == ImplementationStatus.OPERATIONAL
    assert meta.operational is True
    assert any(f.name == "password" for f in meta.required_credential_fields)
    morgan = providers["morgan"]
    assert morgan.provider_name == "Morgan Capital"
    assert morgan.implementation_status == ImplementationStatus.OPERATIONAL
    assert morgan.operational is True
    cs = providers["cardinalstone"]
    assert cs.provider_name == "CardinalStone"
    assert cs.implementation_status == ImplementationStatus.OPERATIONAL
    assert cs.operational is True
    assert "core2.cardinalstone.com" in cs.approved_domains
    vt = providers["vetiva"]
    assert vt.provider_name == "Vetiva"
    assert vt.implementation_status == ImplementationStatus.SCAFFOLDED
    assert vt.operational is False
    assert "client.vetiva.com" in vt.approved_domains
    for pid in ("wstc", "sisl"):
        iw = providers[pid]
        assert iw.implementation_status == ImplementationStatus.SCAFFOLDED
        assert iw.operational is False
    assert "customerportal.wstc.com.ng" in providers["wstc"].approved_domains
    assert "www.sislbrokerage.com" in providers["sisl"].approved_domains
    cd = providers["cordros"]
    assert cd.implementation_status == ImplementationStatus.SCAFFOLDED and cd.operational is False
    assert "unified.cordros.com" in cd.approved_domains
    inw = providers["investnow"]
    assert inw.implementation_status == ImplementationStatus.SCAFFOLDED and inw.operational is False
    example = providers["example_broker"]
    assert example.implementation_status == ImplementationStatus.REGISTERED
    assert example.operational is False


def test_get_provider_metadata():
    meta = get_provider_metadata("afrinvestor")
    assert meta.mfa_supported is True
    assert "afrinvestor.com" in meta.approved_domains
    morgan = get_provider_metadata("morgan")
    assert "morgancapitalgroup.com" in morgan.approved_domains
    assert morgan.mfa_supported is True


def test_example_broker_not_operational():
    """Registry presence must not imply operational support for scaffolds."""
    meta = get_provider_metadata("example_broker")
    assert meta.operational is False
    assert meta.implementation_status != ImplementationStatus.OPERATIONAL
    # morgan is operational — covered in test_afrinvestor_registered / test_morgan_parser
