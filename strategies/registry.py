"""
Strategy Registry - discover and load strategy plugins by name.

Usage:
    registry = StrategyRegistry()
    registry.auto_discover()  # finds all strategies/ subpackages
    strategy = registry.create('hybrid_4h_maker', config=my_config)
"""

from typing import Optional, Type

from strategies.core.base import StrategyPlugin


class StrategyRegistry:
    """Registry for strategy plugins."""

    def __init__(self):
        self._strategies: dict[str, Type[StrategyPlugin]] = {}

    def register(self, strategy_cls: Type[StrategyPlugin]) -> None:
        """Register a strategy class by its name property."""
        # Instantiate temporarily to get the name, or use class-level attribute
        # We require the class to have a NAME class attribute or we instantiate
        name = getattr(strategy_cls, 'NAME', None)
        if name is None:
            # Try instantiating with no args to read the property
            raise ValueError(
                f"{strategy_cls.__name__} must define a NAME class attribute "
                f"for registry discovery."
            )
        if name in self._strategies:
            raise ValueError(f"Strategy '{name}' already registered.")
        self._strategies[name] = strategy_cls

    def get(self, name: str) -> Optional[Type[StrategyPlugin]]:
        """Look up a strategy class by name."""
        return self._strategies.get(name)

    def create(self, name: str, config=None) -> StrategyPlugin:
        """Create an instance of a registered strategy."""
        cls = self._strategies.get(name)
        if cls is None:
            available = ', '.join(sorted(self._strategies.keys())) or '(none)'
            raise KeyError(
                f"Strategy '{name}' not found. Available: {available}"
            )
        instance = cls()
        if config is not None:
            instance.configure(config)
        return instance

    def list_strategies(self) -> list[str]:
        """Return all registered strategy names."""
        return sorted(self._strategies.keys())

    def auto_discover(self) -> None:
        """
        Auto-discover strategies from known subpackages.

        Imports each strategy subpackage and registers any
        StrategyPlugin subclass that defines a NAME attribute.
        """
        import importlib

        packages = [
            'strategies.hybrid_4h_maker',
            'strategies.composite_scoring',
        ]

        for pkg in packages:
            try:
                mod = importlib.import_module(pkg)
                # Look for classes with NAME attribute
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, StrategyPlugin)
                        and attr is not StrategyPlugin
                        and hasattr(attr, 'NAME')
                    ):
                        if attr.NAME not in self._strategies:
                            self.register(attr)
            except ImportError:
                # Strategy package not yet implemented - skip
                pass


# Module-level singleton
_default_registry = None


def get_registry() -> StrategyRegistry:
    """Get or create the default registry singleton."""
    global _default_registry
    if _default_registry is None:
        _default_registry = StrategyRegistry()
        _default_registry.auto_discover()
    return _default_registry
