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
    RiskEvent,
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

        # Per-symbol rolling buffer of OHLCV dicts (aggregated candles)
        self._bars: dict[str, deque] = {}

        # Per-symbol bar counter (for cooldown tracking)
        self._bar_idx: dict[str, int] = {}

        # Aggregation buffer: accumulates 1-min candles until interval reached
        self._agg_buffer: dict[str, list] = {}

        # 24h ROC from live ticker (only populated in live mode)
        self._roc_24h: dict[str, float] = {}

        # Per-symbol momentum cooldown: bar_idx at which momo signals re-enable
        self._momo_cooldown: dict[str, int] = {}

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

    def on_risk_event(self, event: RiskEvent) -> None:
        """Handle exit manager loss exits — set post-loss buy lockout.

        When the exit manager closes a position at a loss (soft_stop,
        hard_stop, trailing_stop), we block re-buying that symbol for
        a configurable number of bars to prevent the death spiral where
        oversold indicators immediately trigger a re-buy.
        """
        if event.event_type != "exit_triggered":
            return
        symbol = event.metadata.get("symbol")
        reason = event.reason
        if not symbol or symbol not in self._bar_idx:
            return
        bar_idx = self._bar_idx[symbol]
        self._guardrails.record_loss_exit(symbol, bar_idx, reason, self._config)

    def get_state(self) -> dict:
        return {
            "guardrails": self._guardrails.get_state(),
            "bar_idx": dict(self._bar_idx),
            "roc_24h": dict(self._roc_24h),
            "momo_cooldown": dict(self._momo_cooldown),
        }

    def load_state(self, state: dict) -> None:
        if "guardrails" in state:
            self._guardrails.load_state(state["guardrails"])
        self._bar_idx = dict(state.get("bar_idx", {}))
        self._roc_24h = dict(state.get("roc_24h", {}))
        self._momo_cooldown = dict(state.get("momo_cooldown", {}))

    # ------------------------------------------------------------------
    # Core evaluation
    # ------------------------------------------------------------------

    def _process_candle(self, candle: Candle) -> Signal | None:
        """Accumulate candle in buffer, evaluate if enough bars.

        When ``candle_interval_minutes > 1``, incoming 1-min candles are
        aggregated into N-minute bars before being appended to the rolling
        indicator buffer.
        """
        symbol = candle.symbol
        cfg = self._config
        interval = cfg.candle_interval_minutes

        # Initialize buffers for symbol
        if symbol not in self._bars:
            self._bars[symbol] = deque(maxlen=cfg.buffer_size)
            self._bar_idx[symbol] = 0

        bar_dict = {
            "timestamp": candle.timestamp,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
        }

        if interval <= 1:
            # Direct append — original 1-min behavior
            self._bars[symbol].append(bar_dict)
            self._bar_idx[symbol] += 1
        else:
            # Aggregate N one-minute candles into one N-minute bar
            if symbol not in self._agg_buffer:
                self._agg_buffer[symbol] = []
            self._agg_buffer[symbol].append(bar_dict)

            if len(self._agg_buffer[symbol]) < interval:
                return None  # Waiting for more 1-min candles

            # Aggregate the buffered candles
            buf = self._agg_buffer[symbol]
            agg = {
                "timestamp": buf[-1]["timestamp"],
                "open": buf[0]["open"],
                "high": max(b["high"] for b in buf),
                "low": min(b["low"] for b in buf),
                "close": buf[-1]["close"],
                "volume": sum(b["volume"] for b in buf),
            }
            self._agg_buffer[symbol] = []
            self._bars[symbol].append(agg)
            self._bar_idx[symbol] += 1

        # Not enough data yet
        if len(self._bars[symbol]) < cfg.min_bars:
            return None

        return self._evaluate(symbol, candle)

    def _evaluate(self, symbol: str, candle: Candle) -> Signal | None:
        """Run indicators → momentum check → scoring → guardrails → signal."""
        cfg = self._config
        bar_idx = self._bar_idx[symbol]

        # Post-loss buy lockout — blocks ALL buy signal paths (momentum + composite)
        buy_locked = self._guardrails.is_buy_locked_by_loss(symbol, bar_idx)

        # Build DataFrame from buffer
        df = pd.DataFrame(list(self._bars[symbol]))
        df.set_index("timestamp", inplace=True)

        # Compute indicators
        ind = compute_indicators(df, cfg)
        if not ind:
            return None

        # --- Volume confirmation gate (gates ALL buy paths) ---
        volume_ok = True
        rvol = ind.get("_RVOL", 0.0)
        if cfg.volume_confirm_buy:
            volume_available = rvol > 0  # 0 means no volume data
            if volume_available and rvol < cfg.volume_confirm_threshold:
                volume_ok = False
                logger.debug(
                    "Volume gate: %s RVOL=%.2f < %.2f — buys blocked",
                    symbol, rvol, cfg.volume_confirm_threshold,
                )

        # --- Priority: 20-minute momentum scalps ---
        roc_value = ind.get("_ROC")
        rsi_value = ind.get("_RSI")

        # Momentum cooldown: skip ROC momentum signals if one was emitted
        # for this symbol within the last cooldown_bars candles
        momo_cd = self._momo_cooldown.get(symbol, -1)
        momo_ok = bar_idx >= momo_cd

        if momo_ok and roc_value is not None and rsi_value is not None:
            lo, hi = cfg.roc_20m_rsi_buy_range
            if not buy_locked and volume_ok and roc_value > cfg.roc_20m_buy_threshold and lo <= rsi_value <= hi:
                self._momo_cooldown[symbol] = bar_idx + cfg.cooldown_bars
                return self._make_signal(
                    Direction.BUY, symbol, candle, "roc_momo_20m",
                    {"roc_20m": roc_value, "rsi": rsi_value, "rvol": rvol},
                )
            lo, hi = cfg.roc_20m_rsi_sell_range
            if roc_value < cfg.roc_20m_sell_threshold and lo <= rsi_value <= hi:
                self._momo_cooldown[symbol] = bar_idx + cfg.cooldown_bars
                return self._make_signal(
                    Direction.SELL, symbol, candle, "roc_momo_20m",
                    {"roc_20m": roc_value, "rsi": rsi_value},
                )

        # --- Priority: 24-hour momentum runners ---
        roc_24h = self._roc_24h.get(symbol)
        if momo_ok and roc_24h is not None and rsi_value is not None:
            lo, hi = cfg.roc_24h_rsi_range
            if not buy_locked and volume_ok and roc_24h > cfg.roc_24h_buy_threshold and lo <= rsi_value <= hi:
                self._momo_cooldown[symbol] = bar_idx + cfg.cooldown_bars
                return self._make_signal(
                    Direction.BUY, symbol, candle, "roc_momo_24h",
                    {"roc_24h": roc_24h, "rsi": rsi_value, "rvol": rvol},
                )
            if roc_24h < cfg.roc_24h_sell_threshold and lo <= rsi_value <= hi:
                self._momo_cooldown[symbol] = bar_idx + cfg.cooldown_bars
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

        # --- Volume confirmation gate (composite buy path) ---
        if direction == Direction.BUY and not volume_ok:
            return None

        # --- Red-day gate: block buys when 24h change is negative ---
        if direction == Direction.BUY and not cfg.allow_buys_on_red_day:
            roc_24h = self._roc_24h.get(symbol)
            if roc_24h is None:
                logger.debug("Buy blocked for %s: no 24h price data", symbol)
                return None
            if roc_24h < 0:
                logger.debug(
                    "Buy blocked for %s: 24h price down (%.2f%%)", symbol, roc_24h,
                )
                return None

        reason = note or "score"
        metadata = {
            "trigger": "score",
            "buy_score": buy_score,
            "sell_score": sell_score,
            "buy_signal": buy_signal,
            "sell_signal": sell_signal,
            "guardrail": note,
            "rvol": rvol,
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
