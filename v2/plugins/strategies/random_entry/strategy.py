"""Random Entry Strategy — baseline for diagnostic comparison.

Emits buy signals at random intervals (Poisson-distributed) to serve as a
control experiment. Uses the same exit manager, stops, and sizing as the
real strategy. Does NOT emit sell signals — all exits handled by exit manager.

If random entries perform similarly to composite scoring → exit manager is the
problem, not entries. If random performs worse → indicators have value.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from v2.core import registry
from v2.core.interfaces import Strategy
from v2.core.types import (
    Candle,
    Direction,
    Fill,
    OrderType,
    Signal,
    TickerEvent,
)

logger = logging.getLogger(__name__)


@registry.plugin("strategy", "random_entry")
class RandomEntryStrategy(Strategy):
    """Random buy timing baseline — Poisson-distributed entries."""

    name = "random_entry"
    version = "1.0"

    def __init__(self, event_bus=None, **kwargs: Any) -> None:
        self._bus = event_bus
        self._mean_interval = 5000  # Mean bars between buys per symbol
        self._seed = 42
        self._rng: np.random.RandomState | None = None

        # Per-symbol state
        self._next_buy_bar: dict[str, int] = {}  # Bar index at which to buy
        self._bar_idx: dict[str, int] = {}
        self._has_position: set[str] = set()  # Avoid double-buying

        # Counters
        self._buys_emitted = 0

    def configure(self, config: Any) -> None:
        if isinstance(config, dict):
            inner = config.get("config", config)
            self._mean_interval = inner.get("mean_interval_bars", 5000)
            self._seed = inner.get("seed", 42)
        self._rng = np.random.RandomState(self._seed)

    def warmup_bars(self) -> dict[str, int]:
        return {"1m": 1}

    def start(self) -> None:
        if self._rng is None:
            self._rng = np.random.RandomState(self._seed)

    def stop(self) -> None:
        pass

    def on_candle(self, candle: Candle, indicators: dict) -> Signal | None:
        symbol = candle.symbol

        # Initialize per-symbol state
        if symbol not in self._bar_idx:
            self._bar_idx[symbol] = 0
            self._next_buy_bar[symbol] = self._sample_next_interval()

        self._bar_idx[symbol] += 1
        bar_idx = self._bar_idx[symbol]

        # Don't double-buy
        if symbol in self._has_position:
            return None

        # Check if it's time to buy
        if bar_idx >= self._next_buy_bar[symbol]:
            self._next_buy_bar[symbol] = bar_idx + self._sample_next_interval()
            self._has_position.add(symbol)
            self._buys_emitted += 1

            return Signal(
                direction=Direction.BUY,
                symbol=symbol,
                timestamp=candle.timestamp,
                price=candle.close,
                order_type=OrderType.LIMIT,
                reason="random_entry",
                metadata={"trigger": "random_entry", "bar_idx": bar_idx},
            )

        return None

    def on_fill(self, fill: Fill) -> Signal | None:
        """Track position state from fills."""
        if fill.side.value == "sell":
            self._has_position.discard(fill.symbol)
        return None

    def on_ticker(self, ticker: TickerEvent) -> Signal | None:
        return None

    def get_state(self) -> dict:
        return {}

    def load_state(self, state: dict) -> None:
        pass

    def _sample_next_interval(self) -> int:
        """Sample next buy interval from exponential distribution."""
        return max(1, int(self._rng.exponential(self._mean_interval)))

    def get_statistics(self) -> dict:
        return {
            "strategy": "random_entry",
            "buys_emitted": self._buys_emitted,
            "mean_interval_bars": self._mean_interval,
            "seed": self._seed,
        }
