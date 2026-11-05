"""Provider connector registry and discovery."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import TYPE_CHECKING, Callable, TypeVar

from app.connectors.base import BrokerConnector
from app.connectors.models import ImplementationStatus, ProviderMetadata
from app.core.errors import AppError, ErrorCode

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BrokerConnector)

_REGISTRY: dict[str, type[BrokerConnector]] = {}
_DISCOVERED = False


def register_connector(provider_id: str) -> Callable[[type[T]], type[T]]:
    """Decorator to register a connector class under a stable provider_id."""

    def decorator(cls: type[T]) -> type[T]:
        if not issubclass(cls, BrokerConnector):
            raise TypeError(f"{cls.__name__} must subclass BrokerConnector")
        key = provider_id.lower().strip()
        if key in _REGISTRY and _REGISTRY[key] is not cls:
            raise ValueError(f"Duplicate connector registration for '{key}'")
        cls.provider_id = key
        _REGISTRY[key] = cls
        logger.debug("Registered connector: %s -> %s", key, cls.__name__)
        return cls

    return decorator


def _ensure_module_connectors_registered(mod: object) -> None:
    """Re-bind connector classes after ``clear_registry_for_tests`` (import is a no-op)."""
    for attr_name in dir(mod):
        attr = getattr(mod, attr_name, None)
        if not isinstance(attr, type):
            continue
        if not issubclass(attr, BrokerConnector) or attr is BrokerConnector:
            continue
        key = (getattr(attr, "provider_id", None) or "").lower().strip()
        if key:
            _REGISTRY[key] = attr


def discover_connectors() -> None:
    """Import all connector packages under app.connectors to trigger registration."""
    global _DISCOVERED
    if _DISCOVERED:
        return
    import app.connectors as connectors_pkg

    for module_info in pkgutil.iter_modules(connectors_pkg.__path__, connectors_pkg.__name__ + "."):
        name = module_info.name.rsplit(".", 1)[-1]
        if name in {"base", "registry", "models"}:
            continue
        try:
            mod = importlib.import_module(f"{module_info.name}.connector")
            _ensure_module_connectors_registered(mod)
            logger.info("Loaded connector package: %s", module_info.name)
        except ModuleNotFoundError:
            # Package without connector.py is fine (e.g. shared helpers)
            logger.debug("No connector module in %s", module_info.name)
        except Exception:
            logger.exception("Failed to load connector package %s", module_info.name)
    _DISCOVERED = True


def get_connector_class(provider_id: str) -> type[BrokerConnector]:
    discover_connectors()
    key = provider_id.lower().strip()
    if key not in _REGISTRY:
        raise AppError(
            ErrorCode.PROVIDER_NOT_FOUND,
            f"Provider '{provider_id}' is not registered",
            status_code=404,
        )
    return _REGISTRY[key]


def create_connector(provider_id: str) -> BrokerConnector:
    cls = get_connector_class(provider_id)
    return cls()


def list_provider_metadata() -> list[ProviderMetadata]:
    discover_connectors()
    results: list[ProviderMetadata] = []
    for cls in _REGISTRY.values():
        meta = cls.metadata()
        # Ensure operational flag matches status
        meta.operational = meta.implementation_status == ImplementationStatus.OPERATIONAL
        results.append(meta)
    return sorted(results, key=lambda m: m.provider_id)


def get_provider_metadata(provider_id: str) -> ProviderMetadata:
    cls = get_connector_class(provider_id)
    meta = cls.metadata()
    meta.operational = meta.implementation_status == ImplementationStatus.OPERATIONAL
    return meta


def is_registered(provider_id: str) -> bool:
    discover_connectors()
    return provider_id.lower().strip() in _REGISTRY


def clear_registry_for_tests() -> None:
    """Test helper — do not use in production."""
    global _DISCOVERED
    _REGISTRY.clear()
    _DISCOVERED = False
