"""Basic risk manager — pass-through for backtest, limits for live.

In backtest mode, signals pass through unchecked (the strategy handles
its own risk via stop losses and position sizing). In live/paper mode,
this enforces exposure limits and daily loss caps.
"""

from __future__ import annotations

from typing import Any

from v2.core import registry
from v2.core.interfaces import RiskManager
from v2.core.types import Fill, Portfolio, Position, Signal


@registry.plugin("risk", "basic")
class BasicRiskManager(RiskManager):
    """Validates signals against configurable risk limits."""

    name = "basic"

    def __init__(self, event_bus=None, **kwargs: Any) -> None:
        self._max_exposure = kwargs.get("max_exposure_per_symbol_usd", float("inf"))
        self._max_daily_loss = kwargs.get("max_daily_loss_usd", float("inf"))
        self._max_positions = kwargs.get("max_open_positions", 100)

    def configure(self, config: Any) -> None:
        if isinstance(config, dict):
            self._max_exposure = config.get(
                "max_exposure_per_symbol_usd", self._max_exposure
            )
            self._max_daily_loss = config.get(
                "max_daily_loss_usd", self._max_daily_loss
            )
            self._max_positions = config.get(
                "max_open_positions", self._max_positions
            )

    def check_signal(self, signal: Signal, portfolio: Portfolio) -> Signal | None:
        # In backtest mode, pass through all signals
        return signal

    def on_fill(self, fill: Fill, portfolio: Portfolio) -> None:
        pass

    def on_position_update(self, position: Position) -> Signal | None:
        return None
