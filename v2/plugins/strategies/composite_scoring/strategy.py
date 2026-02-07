"""Composite Scoring Strategy — v2 plugin.

Ports the production sighook/signal_manager.py composite scoring system
into a self-contained v2 Strategy plugin.

The strategy maintains an internal rolling buffer of candle data, computes
its own indicators (BB, MACD, RSI, ROC, W-Bottom/M-Top, Swing), runs
weighted scoring with multi-indicator confirmation, and applies guardrails
(hysteresis, cooldown).

Two priority momentum strategies (20m ROC and 24h ROC) can bypass the
composite scoring with early returns.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

import pandas as pd

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
from v2.plugins.strategies.composite_scoring.config import CompositeScoreConfig
from v2.plugins.strategies.composite_scoring.guardrails import Guardrails
from v2.plugins.strategies.composite_scoring.indicators import compute_indicators
from v2.plugins.strategies.composite_scoring.scoring import compute_scores

logger = logging.getLogger(__name__)


@registry.plugin("strategy", "composite_scoring")
class CompositeScoringStrategy(Strategy):
    """v2 composite scoring strategy with multi-indicator confirmation.

    Processes 1-minute candles, computes indicators internally,
    applies weighted scoring + guardrails, emits buy/sell signals.
    """

    name = "composite_scoring"
    version = "1.0"

    def __init__(self, event_bus=None, **kwargs: Any) -> None:
        self._bus = event_bus
        self._config = CompositeScoreConfig()
        self._guardrails = Guardrails()
        self._configured = False

        # Per-symbol rolling buffer of OHLCV dicts
        self._bars: dict[str, deque] = {}

        # Per-symbol bar counter (for cooldown tracking)
        self._bar_idx: dict[str, int] = {}

        # 24h ROC from live ticker (only populated in live mode)
        self._roc_24h: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Strategy ABC
    # ------------------------------------------------------------------

    def configure(self, config: Any) -> None:
        if isinstance(config, CompositeScoreConfig):
            self._config = config
        elif isinstance(config, dict):
            inner = config.get("config", config)
            if isinstance(inner, CompositeScoreConfig):
                self._config = inner
            elif inner:
                self._config = CompositeScoreConfig(**inner)
            else:
                self._config = CompositeScoreConfig()
        else:
            self._config = CompositeScoreConfig()
        self._configured = True

    def warmup_bars(self) -> dict[str, int]:
        return {"1m": self._config.min_bars}

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def on_candle(self, candle: Candle, indicators: dict) -> Signal | None:
        """Live-mode entry point — accumulate candle, evaluate."""
        return self._process_candle(candle)

    def on_backtest_bar(
        self,
        symbol: str,
        candle: Candle,
        indicators: dict[str, dict],
        context: dict,
    ) -> Signal | None:
        """Backtest entry point — same logic as on_candle."""
        return self._process_candle(candle)

    def on_fill(self, fill: Fill) -> Signal | None:
        return None

    def on_ticker(self, ticker: TickerEvent) -> Signal | None:
        """Capture 24h price change for the 24h ROC momentum strategy."""
        if ticker.change_24h_pct is not None:
            self._roc_24h[ticker.symbol] = ticker.change_24h_pct
        return None

    def get_state(self) -> dict:
        return {
            "guardrails": self._guardrails.get_state(),
            "bar_idx": dict(self._bar_idx),
            "roc_24h": dict(self._roc_24h),
        }

    def load_state(self, state: dict) -> None:
        if "guardrails" in state:
            self._guardrails.load_state(state["guardrails"])
        self._bar_idx = dict(state.get("bar_idx", {}))
        self._roc_24h = dict(state.get("roc_24h", {}))

    # ------------------------------------------------------------------
    # Core evaluation
    # ------------------------------------------------------------------

    def _process_candle(self, candle: Candle) -> Signal | None:
        """Accumulate candle in buffer, evaluate if enough bars."""
        symbol = candle.symbol
        cfg = self._config

        # Initialize buffer for symbol
        if symbol not in self._bars:
            self._bars[symbol] = deque(maxlen=cfg.buffer_size)
            self._bar_idx[symbol] = 0

        # Append candle to buffer
        self._bars[symbol].append({
            "timestamp": candle.timestamp,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
        })
        self._bar_idx[symbol] += 1

        # Not enough data yet
        if len(self._bars[symbol]) < cfg.min_bars:
            return None

        return self._evaluate(symbol, candle)

    def _evaluate(self, symbol: str, candle: Candle) -> Signal | None:
        """Run indicators → momentum check → scoring → guardrails → signal."""
        cfg = self._config
        bar_idx = self._bar_idx[symbol]

        # Build DataFrame from buffer
        df = pd.DataFrame(list(self._bars[symbol]))
        df.set_index("timestamp", inplace=True)

        # Compute indicators
        ind = compute_indicators(df, cfg)
        if not ind:
            return None

        # --- Priority: 20-minute momentum scalps ---
        roc_value = ind.get("_ROC")
        rsi_value = ind.get("_RSI")

        if roc_value is not None and rsi_value is not None:
            lo, hi = cfg.roc_20m_rsi_buy_range
            if roc_value > cfg.roc_20m_buy_threshold and lo <= rsi_value <= hi:
                return self._make_signal(
                    Direction.BUY, symbol, candle, "roc_momo_20m",
                    {"roc_20m": roc_value, "rsi": rsi_value},
                )
            lo, hi = cfg.roc_20m_rsi_sell_range
            if roc_value < cfg.roc_20m_sell_threshold and lo <= rsi_value <= hi:
                return self._make_signal(
                    Direction.SELL, symbol, candle, "roc_momo_20m",
                    {"roc_20m": roc_value, "rsi": rsi_value},
                )

        # --- Priority: 24-hour momentum runners ---
        roc_24h = self._roc_24h.get(symbol)
        if roc_24h is not None and rsi_value is not None:
            lo, hi = cfg.roc_24h_rsi_range
            if roc_24h > cfg.roc_24h_buy_threshold and lo <= rsi_value <= hi:
                return self._make_signal(
                    Direction.BUY, symbol, candle, "roc_momo_24h",
                    {"roc_24h": roc_24h, "rsi": rsi_value},
                )
            if roc_24h < cfg.roc_24h_sell_threshold and lo <= rsi_value <= hi:
                return self._make_signal(
                    Direction.SELL, symbol, candle, "roc_momo_24h",
                    {"roc_24h": roc_24h, "rsi": rsi_value},
                )

        # --- Composite scoring ---
        buy_score, sell_score, buy_signal, sell_signal, components = compute_scores(
            ind, cfg,
        )

        # --- Guardrails ---
        buy_signal, sell_signal, action, note = self._guardrails.apply(
            symbol, buy_signal, sell_signal, buy_score, sell_score, bar_idx, cfg,
        )

        if action == "hold":
            return None

        direction = Direction.BUY if action == "buy" else Direction.SELL
        reason = note or "score"
        metadata = {
            "trigger": "score",
            "buy_score": buy_score,
            "sell_score": sell_score,
            "buy_signal": buy_signal,
            "sell_signal": sell_signal,
            "guardrail": note,
        }

        return self._make_signal(direction, symbol, candle, reason, metadata)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_signal(
        direction: Direction,
        symbol: str,
        candle: Candle,
        reason: str,
        metadata: dict,
    ) -> Signal:
        return Signal(
            direction=direction,
            symbol=symbol,
            timestamp=candle.timestamp,
            price=candle.close,
            order_type=OrderType.LIMIT,
            reason=reason,
            metadata=metadata,
        )
