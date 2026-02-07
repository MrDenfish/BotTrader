"""Maker-only execution manager.

In backtest mode, the strategy handles fill simulation internally,
so this is a pass-through. In live mode, this will handle post-only
price adjustment, retries, and order submission.
"""

from __future__ import annotations

from typing import Any

from v2.core import registry
from v2.core.interfaces import ExecutionManager, ExchangeAdapter
from v2.core.types import Order, Signal


@registry.plugin("execution", "maker_only")
class MakerOnlyExecution(ExecutionManager):
    """Post-only limit order execution (stub for backtest)."""

    name = "maker_only"

    def __init__(self, event_bus=None, **kwargs: Any) -> None:
        self._max_retries = kwargs.get("max_retries", 3)
        self._post_only = kwargs.get("post_only", True)

    def configure(self, config: Any) -> None:
        if isinstance(config, dict):
            self._max_retries = config.get("max_retries", self._max_retries)
            self._post_only = config.get("post_only", self._post_only)

    async def execute_signal(
        self, signal: Signal, exchange: ExchangeAdapter
    ) -> Order | None:
        # In backtest mode, signals are informational only —
        # the strategy's state machine handles fill simulation.
        return None
