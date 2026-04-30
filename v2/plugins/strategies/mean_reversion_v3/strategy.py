"""Mean-Reversion v3 Strategy plugin.

Two-stage architecture:

    Stage 1 (regime gate):
        Supertrend in uptrend mode AND ADX < adx_max_threshold (range-bound).
        If either fails, no buy is considered for this bar.

    Stage 2 (composite scoring):
        Weighted sum over 6 mean-reversion indicators. Both score target
        and minimum-indicator-count must be met. Volume confirmation
        (RVOL >= threshold) gates the final buy.

The plugin is BUY-only. Profits are taken via fixed take-profit in the
exit_manager. Hard stops, breakeven moves, and time limits are also
handled by the exit_manager.
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
from v2.plugins.strategies.mean_reversion_v3.config import MeanReversionV3Config
from v2.plugins.strategies.mean_reversion_v3.indicators import compute_indicators
from v2.plugins.strategies.mean_reversion_v3.scoring import compute_buy_score

logger = logging.getLogger(__name__)


@registry.plugin("strategy", "mean_reversion_v3")
class MeanReversionV3Strategy(Strategy):
    """Mean-reversion strategy with Supertrend regime gate."""

    name = "mean_reversion_v3"
    version = "1.0"

    def __init__(self, event_bus=None, **kwargs: Any) -> None:
        self._bus = event_bus
        self._config = MeanReversionV3Config()
        self._configured = False

        self._bars: dict[str, deque] = {}
        self._bar_idx: dict[str, int] = {}
        self._agg_buffer: dict[str, list] = {}

        # Cooldown after recent buy or loss exit
        self._last_buy_bar: dict[str, int] = {}
        self._loss_lockout_until: dict[str, int] = {}

        self._signal_counts: dict[str, int] = {"buy": 0, "sell": 0, "hold": 0}

    # ------------------------------------------------------------------
    # Strategy ABC
    # ------------------------------------------------------------------

    def configure(self, config: Any) -> None:
        if isinstance(config, MeanReversionV3Config):
            self._config = config
        elif isinstance(config, dict):
            inner = config.get("config", config)
            if isinstance(inner, MeanReversionV3Config):
                self._config = inner
            elif inner:
                self._config = MeanReversionV3Config(**inner)
            else:
                self._config = MeanReversionV3Config()
        else:
            self._config = MeanReversionV3Config()
        self._configured = True

    def warmup_bars(self) -> dict[str, int]:
        return {"1m": self._config.min_bars}

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def on_candle(self, candle: Candle, indicators: dict) -> Signal | None:
        return self._process_candle(candle)

    def on_backtest_bar(
        self,
        symbol: str,
        candle: Candle,
        indicators: dict[str, dict],
        context: dict,
    ) -> Signal | None:
        return self._process_candle(candle)

    def on_fill(self, fill: Fill) -> Signal | None:
        return None

    def on_ticker(self, ticker: TickerEvent) -> Signal | None:
        return None

    def on_risk_event(self, event: RiskEvent) -> None:
        """Set post-loss buy lockout when exit_manager closes at a loss."""
        if event.event_type != "exit_triggered":
            return
        symbol = event.metadata.get("symbol")
        reason = event.reason
        if not symbol or symbol not in self._bar_idx:
            return
        # Only lock out on loss-flavored exits
        loss_exits = {"hard_stop", "breakeven_stop", "stale_exit"}
        if reason not in loss_exits:
            return
        scale = self._config.loss_lockout_scale.get(reason, 1.0)
        bars = int(self._config.loss_lockout_bars * scale)
        self._loss_lockout_until[symbol] = self._bar_idx[symbol] + bars
        logger.info(
            "Loss lockout: %s blocked for %d bars after %s",
            symbol, bars, reason,
        )

    def get_state(self) -> dict:
        return {
            "bar_idx": dict(self._bar_idx),
            "last_buy_bar": dict(self._last_buy_bar),
            "loss_lockout_until": dict(self._loss_lockout_until),
        }

    def load_state(self, state: dict) -> None:
        self._bar_idx = dict(state.get("bar_idx", {}))
        self._last_buy_bar = dict(state.get("last_buy_bar", {}))
        self._loss_lockout_until = dict(state.get("loss_lockout_until", {}))

    # ------------------------------------------------------------------
    # Core evaluation
    # ------------------------------------------------------------------

    def _process_candle(self, candle: Candle) -> Signal | None:
        symbol = candle.symbol
        cfg = self._config
        interval = cfg.candle_interval_minutes

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
            self._bars[symbol].append(bar_dict)
            self._bar_idx[symbol] += 1
        else:
            if symbol not in self._agg_buffer:
                self._agg_buffer[symbol] = []
            self._agg_buffer[symbol].append(bar_dict)
            if len(self._agg_buffer[symbol]) < interval:
                return None
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

        if len(self._bars[symbol]) < cfg.min_bars:
            return None

        return self._evaluate(symbol, candle)

    def _evaluate(self, symbol: str, candle: Candle) -> Signal | None:
        cfg = self._config
        bar_idx = self._bar_idx[symbol]

        # Cooldown / lockout checks
        lockout_until = self._loss_lockout_until.get(symbol, 0)
        if bar_idx < lockout_until:
            return None
        last_buy = self._last_buy_bar.get(symbol, -10**9)
        if bar_idx - last_buy < cfg.cooldown_bars:
            return None

        df = pd.DataFrame(list(self._bars[symbol]))
        df.set_index("timestamp", inplace=True)

        ind = compute_indicators(df, cfg)
        if not ind:
            return None

        # ============================================================
        # Stage 1: REGIME GATE
        # ============================================================
        st_dir = ind.get("_Supertrend_dir", 0)
        adx_value = ind.get("_ADX", 0.0)
        rvol = ind.get("_RVOL", 0.0)

        # Supertrend must be in uptrend (+1)
        if st_dir != 1:
            logger.debug("Supertrend gate: %s direction=%d — buy blocked", symbol, st_dir)
            return None

        # ADX must indicate range-bound regime (not strongly trending)
        if adx_value >= cfg.adx_max_threshold:
            logger.debug(
                "ADX gate: %s ADX=%.1f >= %.1f (too strong-trend) — buy blocked",
                symbol, adx_value, cfg.adx_max_threshold,
            )
            return None

        # Volume confirmation (only when volume data is real)
        if cfg.volume_confirm_buy and rvol > 0 and rvol < cfg.volume_confirm_threshold:
            logger.debug(
                "Volume gate: %s RVOL=%.2f < %.2f — buy blocked",
                symbol, rvol, cfg.volume_confirm_threshold,
            )
            return None

        # ============================================================
        # Stage 2: COMPOSITE SCORING
        # ============================================================
        buy_score, buy_signal, components = compute_buy_score(ind, cfg)
        if buy_signal[0] != 1:
            return None

        # Build signal
        buy_fired = components["buy_fired"]
        trigger = "score_high" if buy_fired >= cfg.high_conviction_threshold else "score"

        metadata = {
            "trigger": trigger,
            "buy_score": buy_score,
            "buy_signal": buy_signal,
            "indicator_count": buy_fired,
            "rvol": rvol,
            "adx": adx_value,
            "supertrend_dir": st_dir,
            "supertrend_line": ind.get("_Supertrend_line", 0.0),
            "score_components": components,
            "indicator_snapshot": self._build_snapshot(ind),
            "raw_values": {
                "rsi": ind.get("_RSI", 0.0),
                "roc": ind.get("_ROC", 0.0),
                "rvol": rvol,
                "macd_hist": ind.get("_MACD_Hist", 0.0),
                "bb_upper": ind.get("_upper", 0.0),
                "bb_lower": ind.get("_lower", 0.0),
                "adx": adx_value,
                "supertrend_dir": st_dir,
            },
        }

        self._last_buy_bar[symbol] = bar_idx
        return self._make_signal(Direction.BUY, symbol, candle, "score", metadata)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_snapshot(self, ind: dict) -> dict:
        snapshot = {}
        for name, val in ind.items():
            if name.startswith("_"):
                continue
            if isinstance(val, tuple) and len(val) == 3:
                snapshot[name] = {"decision": val[0], "value": val[1], "threshold": val[2]}
        return snapshot

    def _make_signal(
        self,
        direction: Direction,
        symbol: str,
        candle: Candle,
        reason: str,
        metadata: dict,
    ) -> Signal:
        self._signal_counts[direction.value] = self._signal_counts.get(direction.value, 0) + 1
        return Signal(
            direction=direction,
            symbol=symbol,
            timestamp=candle.timestamp,
            price=candle.close,
            order_type=OrderType.LIMIT,
            reason=reason,
            metadata=metadata,
        )

    def get_statistics(self) -> dict:
        return {
            "signals_buy": self._signal_counts.get("buy", 0),
            "signals_sell": self._signal_counts.get("sell", 0),
            "signals_hold": self._signal_counts.get("hold", 0),
            "symbols_tracked": len(self._bars),
            "total_bars_processed": sum(self._bar_idx.values()),
        }
