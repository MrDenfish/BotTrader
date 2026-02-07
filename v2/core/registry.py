"""Plugin discovery and dependency injection.

Plugins register themselves by decorating their class or by being discovered
from known package paths. The registry maps type-name strings (e.g. "coinbase",
"paper", "csv_replay") to concrete classes.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any, Type

from v2.core.interfaces import (
    DataProvider,
    ExchangeAdapter,
    ExecutionManager,
    Observer,
    RiskManager,
    StorageAdapter,
    Strategy,
)

logger = logging.getLogger(__name__)

# Category → (name → class)
_registry: dict[str, dict[str, Type]] = {
    "exchange": {},
    "data": {},
    "strategy": {},
    "risk": {},
    "execution": {},
    "storage": {},
    "observer": {},
}

# Category → expected ABC
_category_abc: dict[str, Type] = {
    "exchange": ExchangeAdapter,
    "data": DataProvider,
    "strategy": Strategy,
    "risk": RiskManager,
    "execution": ExecutionManager,
    "storage": StorageAdapter,
    "observer": Observer,
}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(category: str, name: str, cls: Type) -> None:
    """Register a plugin class under *category* / *name*.

    Raises ``ValueError`` if *category* is unknown or *cls* does not
    subclass the expected ABC for that category.
    """
    if category not in _registry:
        raise ValueError(
            f"Unknown plugin category {category!r}. "
            f"Valid: {list(_registry)}"
        )
    abc = _category_abc[category]
    if not issubclass(cls, abc):
        raise TypeError(
            f"{cls.__name__} does not implement {abc.__name__}"
        )
    _registry[category][name] = cls
    logger.debug("Registered plugin %s/%s → %s", category, name, cls.__name__)


def plugin(category: str, name: str):
    """Class decorator to register a plugin.

    Usage::

        @plugin("exchange", "coinbase")
        class CoinbaseExchange(ExchangeAdapter):
            ...
    """
    def decorator(cls: Type) -> Type:
        register(category, name, cls)
        return cls
    return decorator


# ---------------------------------------------------------------------------
# Lookup / Factory
# ---------------------------------------------------------------------------

def get_class(category: str, name: str) -> Type:
    """Return the registered class for *category* / *name*.

    Raises ``KeyError`` if not found.
    """
    try:
        return _registry[category][name]
    except KeyError:
        available = list(_registry.get(category, {}))
        raise KeyError(
            f"No {category!r} plugin named {name!r}. "
            f"Available: {available}"
        ) from None


def create(category: str, name: str, **kwargs: Any) -> Any:
    """Instantiate a registered plugin."""
    cls = get_class(category, name)
    return cls(**kwargs)


# Convenience factory functions
def create_exchange(name: str, **kw: Any) -> ExchangeAdapter:
    return create("exchange", name, **kw)


def create_data(name: str, **kw: Any) -> DataProvider:
    return create("data", name, **kw)


def create_strategy(name: str, **kw: Any) -> Strategy:
    return create("strategy", name, **kw)


def create_risk(name: str, **kw: Any) -> RiskManager:
    return create("risk", name, **kw)


def create_execution(name: str, **kw: Any) -> ExecutionManager:
    return create("execution", name, **kw)


def create_storage(name: str, **kw: Any) -> StorageAdapter:
    return create("storage", name, **kw)


def create_observer(name: str, **kw: Any) -> Observer:
    return create("observer", name, **kw)


# ---------------------------------------------------------------------------
# Auto-discovery
# ---------------------------------------------------------------------------

def discover_plugins(package_path: str = "v2.plugins") -> None:
    """Import all sub-modules under *package_path* so that ``@plugin``
    decorators execute and classes get registered.
    """
    try:
        package = importlib.import_module(package_path)
    except ModuleNotFoundError:
        logger.warning("Plugin package %s not found", package_path)
        return

    prefix = package.__name__ + "."
    for _importer, modname, ispkg in pkgutil.walk_packages(
        package.__path__, prefix=prefix
    ):
        # Skip __init__ and base modules — they're ABCs, not concrete plugins
        leaf = modname.rsplit(".", 1)[-1]
        if leaf.startswith("_") or leaf == "base":
            continue
        try:
            importlib.import_module(modname)
            logger.debug("Discovered plugin module %s", modname)
        except Exception:
            logger.exception("Failed to import plugin module %s", modname)


def list_plugins(category: str | None = None) -> dict[str, list[str]]:
    """Return registered plugins, optionally filtered by *category*."""
    if category:
        return {category: list(_registry.get(category, {}))}
    return {cat: list(names) for cat, names in _registry.items()}


def clear() -> None:
    """Remove all registrations (useful in tests)."""
    for cat in _registry:
        _registry[cat].clear()
