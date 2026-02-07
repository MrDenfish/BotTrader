"""Hybrid 4h Maker Strategy — v2 plugin adapter.

Wraps the Phase 1 Hybrid4hMakerStrategy behind the v2 Strategy ABC.
The Phase 1 strategy's internal state machine, entry/exit logic, filters,
and diagnostics are reused directly to guarantee trade-for-trade match.

The v2 adapter translates between:
- v2 Candle/Signal types ↔ Phase 1 OHLCV/StrategySignal types
- v2 Strategy ABC ↔ Phase 1 StrategyPlugin ABC
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Ensure backtest/ is importable (Phase 1 uses bare imports)
_project_root = str(Path(__file__).resolve().parents[4])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

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

# Phase 1 imports
from strategies.hybrid_4h_maker.strategy import Hybrid4hMakerStrategy as _Phase1Strategy
from strategies.core.types import OHLCV, StrategySignal, SignalDirection
from backtest.config_4h_hybrid import Hybrid4hConfig


@registry.plugin("strategy", "hybrid_4h_maker")
class Hybrid4hMakerV2(Strategy):
    """v2 adapter for the Phase 1 Hybrid 4h Maker Strategy.

    Delegates all logic to the Phase 1 implementation, translating
    types at the boundary.
    """

    name = "hybrid_4h_maker"
    version = "2.4"

    def __init__(self, event_bus=None, **kwargs: Any) -> None:
        self._bus = event_bus
        self._inner = _Phase1Strategy()
        self._configured = False

    def configure(self, config: Any) -> None:
        if isinstance(config, Hybrid4hConfig):
            self._inner.configure(config)
        elif isinstance(config, dict):
            # Handle nested "config" key from YAML parsing
            inner = config.get("config", config)
            if isinstance(inner, Hybrid4hConfig):
                self._inner.configure(inner)
            elif inner:
                cfg = Hybrid4hConfig(**inner)
                self._inner.configure(cfg)
            else:
                # Empty dict = use defaults
                self._inner.configure(Hybrid4hConfig())
        else:
            self._inner.configure(config)
        self._configured = True

    def warmup_bars(self) -> dict[str, int]:
        return self._inner.warmup_bars()

    def start(self) -> None:
        self._inner.start()

    def stop(self) -> None:
        self._inner.stop()

    def on_candle(self, candle: Candle, indicators: dict) -> Signal | None:
        """Not used in backtest mode — see on_backtest_bar()."""
        return None

    def on_backtest_bar(
        self,
        symbol: str,
        candle: Candle,
        indicators: dict[str, dict],
        context: dict,
    ) -> Signal | None:
        """Backtest-specific entry point with full indicator context.

        Translates v2 Candle → Phase 1 OHLCV, calls evaluate(),
        and translates Phase 1 StrategySignal → v2 Signal.
        """
        bar = OHLCV(
            timestamp=candle.timestamp,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
        )

        result = self._inner.evaluate(symbol, bar, indicators, context)

        if result is None:
            return None

        return self._translate_signal(result)

    def on_fill(self, fill: Fill) -> Signal | None:
        return None

    def on_ticker(self, ticker: TickerEvent) -> Signal | None:
        return None

    def get_state(self) -> dict:
        return self._inner.get_state()

    def load_state(self, state: dict) -> None:
        self._inner.load_state(state)

    # ------------------------------------------------------------------
    # Backtest results access (delegates to Phase 1)
    # ------------------------------------------------------------------

    def get_statistics(self) -> dict:
        return self._inner.get_statistics()

    def print_gate_audit(self) -> str:
        return self._inner.print_gate_audit()

    def print_enhanced_diagnostics(self) -> str:
        return self._inner.print_enhanced_diagnostics()

    @property
    def trades(self):
        return self._inner.trades

    @property
    def expired_setups(self):
        return self._inner.expired_setups

    @property
    def inner(self) -> _Phase1Strategy:
        """Direct access to Phase 1 strategy for full diagnostics."""
        return self._inner

    # ------------------------------------------------------------------
    # Type translation
    # ------------------------------------------------------------------

    @staticmethod
    def _translate_signal(sig: StrategySignal) -> Signal:
        if sig.direction == SignalDirection.BUY:
            direction = Direction.BUY
        elif sig.direction == SignalDirection.SELL:
            direction = Direction.SELL
        else:
            direction = Direction.HOLD

        return Signal(
            direction=direction,
            symbol=sig.symbol,
            timestamp=sig.timestamp,
            price=sig.price,
            qty=sig.qty,
            notional=sig.notional,
            order_type=OrderType.LIMIT,
            reason=sig.reason,
            metadata=sig.metadata if sig.metadata else {},
        )
