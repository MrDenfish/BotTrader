"""Diagnostic observer for backtest analysis — MFE/MAE, regime, correlations.

Subscribes to all events and captures per-trade diagnostic data:
- MFE/MAE (Max Favorable / Adverse Excursion) while position is open
- Post-exit price tracking (60 bars after exit)
- Market regime at entry (SMA slope + ATR percentile)
- Indicator correlation matrix (which indicators fire together)

Register as ``backtest_diagnostics`` observer in YAML config.
Writes diagnostic_trades.jsonl to the configured output directory.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from v2.core import registry
from v2.core.interfaces import Observer
from v2.core.types import CandleEvent, FillEvent, Side

logger = logging.getLogger(__name__)


@dataclass
class TrackedPosition:
    """In-flight position being tracked for MFE/MAE."""
    symbol: str
    entry_price: float
    entry_time: datetime
    entry_metadata: dict
    qty: float
    mfe: float = 0.0  # Max favorable excursion (best unrealized P&L)
    mae: float = 0.0  # Max adverse excursion (worst unrealized P&L)
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    bars_held: int = 0


@dataclass
class PostExitTracker:
    """Track price movement after a trade is closed."""
    symbol: str
    exit_price: float
    exit_time: datetime
    bars_tracked: int = 0
    max_bars: int = 60
    prices: list = field(default_factory=list)

    @property
    def max_favorable_after_exit(self) -> float:
        """Best price seen after exit (how much we left on the table for sells)."""
        if not self.prices:
            return 0.0
        return max(self.prices) - self.exit_price

    @property
    def max_favorable_after_exit_pct(self) -> float:
        if not self.prices or self.exit_price == 0:
            return 0.0
        return (max(self.prices) - self.exit_price) / self.exit_price * 100


@dataclass
class MarketRegime:
    """Market regime snapshot at a point in time."""
    sma_slope: float = 0.0  # SMA(20) slope (positive = uptrend)
    atr_percentile: float = 0.0  # ATR percentile (0-100, high = volatile)
    label: str = ""  # "uptrend_high_vol", "downtrend_low_vol", etc.


@registry.plugin("observer", "backtest_diagnostics")
class BacktestDiagnosticsCollector(Observer):
    """Collects diagnostic data during backtest for post-hoc analysis.

    Writes one JSONL line per closed trade to ``output_dir/diagnostic_trades.jsonl``.
    """

    name = "backtest_diagnostics"

    def __init__(self, event_bus=None, **kwargs: Any) -> None:
        self._bus = event_bus
        self._output_dir = kwargs.get("output_dir", "backtest/diagnostic_output")
        self._post_exit_window = kwargs.get("post_exit_window_bars", 60)

        # Open positions being tracked for MFE/MAE
        self._tracked: dict[str, TrackedPosition] = {}

        # Recently closed positions being tracked post-exit
        self._post_exit: list[PostExitTracker] = []

        # Completed diagnostic records
        self._completed_trades: list[dict] = []

        # Per-symbol rolling price buffer for regime detection
        self._price_buffer: dict[str, deque] = {}
        self._regime_window = 20

        # Indicator firing vectors for correlation matrix
        self._indicator_firings: list[dict[str, int]] = []

    def on_event(self, event: Any) -> None:
        if isinstance(event, FillEvent):
            self._on_fill(event)
        elif isinstance(event, CandleEvent):
            self._on_candle(event)

    def _on_fill(self, event: FillEvent) -> None:
        fill = event.fill
        if fill.side == Side.BUY:
            self._on_buy_fill(fill)
        elif fill.side == Side.SELL:
            self._on_sell_fill(fill)

    def _on_buy_fill(self, fill) -> None:
        """Start tracking a new position for MFE/MAE."""
        symbol = fill.symbol
        price = float(fill.price)

        # Record indicator firings for correlation matrix
        snapshot = fill.metadata.get("indicator_snapshot", {})
        if snapshot:
            firing = {}
            for name, vals in snapshot.items():
                firing[name] = vals.get("decision", 0) if isinstance(vals, dict) else 0
            self._indicator_firings.append(firing)

        # Compute market regime at entry
        regime = self._compute_regime(symbol)

        self._tracked[symbol] = TrackedPosition(
            symbol=symbol,
            entry_price=price,
            entry_time=fill.timestamp,
            entry_metadata={
                **dict(fill.metadata),
                "regime": {
                    "sma_slope": regime.sma_slope,
                    "atr_percentile": regime.atr_percentile,
                    "label": regime.label,
                },
            },
            qty=float(fill.qty),
        )

    def _on_sell_fill(self, fill) -> None:
        """Finalize MFE/MAE tracking, begin post-exit tracking."""
        symbol = fill.symbol
        tracked = self._tracked.pop(symbol, None)
        if tracked is None:
            return

        exit_price = float(fill.price)
        pnl = (exit_price - tracked.entry_price) * tracked.qty
        pnl_pct = (exit_price - tracked.entry_price) / tracked.entry_price * 100 if tracked.entry_price else 0

        record = {
            "symbol": symbol,
            "entry_price": tracked.entry_price,
            "exit_price": exit_price,
            "entry_time": tracked.entry_time.isoformat(),
            "exit_time": fill.timestamp.isoformat(),
            "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 4),
            "bars_held": tracked.bars_held,
            "mfe": round(tracked.mfe, 4),
            "mae": round(tracked.mae, 4),
            "mfe_pct": round(tracked.mfe_pct, 4),
            "mae_pct": round(tracked.mae_pct, 4),
            "entry_trigger": tracked.entry_metadata.get("trigger", ""),
            "exit_reason": fill.metadata.get("signal_reason", ""),
            "entry_metadata": tracked.entry_metadata,
            "exit_metadata": dict(fill.metadata) if fill.metadata else {},
            # Post-exit tracking — filled in later
            "post_exit_max_favorable": 0.0,
            "post_exit_max_favorable_pct": 0.0,
        }

        # Start post-exit tracking
        tracker = PostExitTracker(
            symbol=symbol,
            exit_price=exit_price,
            exit_time=fill.timestamp,
            max_bars=self._post_exit_window,
        )
        self._post_exit.append(tracker)
        # Store reference so we can update the record later
        tracker._record_ref = record

        self._completed_trades.append(record)

    def _on_candle(self, event: CandleEvent) -> None:
        """Update MFE/MAE for open positions and post-exit tracking."""
        candle = event.candle
        symbol = candle.symbol
        close = float(candle.close)
        high = float(candle.high)
        low = float(candle.low)

        # Update price buffer for regime detection
        if symbol not in self._price_buffer:
            self._price_buffer[symbol] = deque(maxlen=max(self._regime_window + 5, 30))
        self._price_buffer[symbol].append({
            "close": close, "high": high, "low": low,
        })

        # Update MFE/MAE for tracked positions
        tracked = self._tracked.get(symbol)
        if tracked:
            tracked.bars_held += 1
            unrealized = (high - tracked.entry_price) * tracked.qty
            unrealized_low = (low - tracked.entry_price) * tracked.qty

            # MFE: best unrealized P&L
            if unrealized > tracked.mfe:
                tracked.mfe = unrealized
                tracked.mfe_pct = (high - tracked.entry_price) / tracked.entry_price * 100

            # MAE: worst unrealized P&L
            if unrealized_low < tracked.mae:
                tracked.mae = unrealized_low
                tracked.mae_pct = (low - tracked.entry_price) / tracked.entry_price * 100

        # Update post-exit tracking
        remaining = []
        for tracker in self._post_exit:
            if tracker.symbol != symbol:
                remaining.append(tracker)
                continue
            tracker.bars_tracked += 1
            tracker.prices.append(close)
            if tracker.bars_tracked >= tracker.max_bars:
                # Finalize post-exit data
                record = getattr(tracker, "_record_ref", None)
                if record:
                    record["post_exit_max_favorable"] = round(tracker.max_favorable_after_exit, 4)
                    record["post_exit_max_favorable_pct"] = round(tracker.max_favorable_after_exit_pct, 4)
                # Don't keep tracking
            else:
                remaining.append(tracker)
        self._post_exit = remaining

    def _compute_regime(self, symbol: str) -> MarketRegime:
        """Compute market regime from recent price history."""
        buf = self._price_buffer.get(symbol)
        if not buf or len(buf) < self._regime_window:
            return MarketRegime(label="insufficient_data")

        closes = [b["close"] for b in buf]
        highs = [b["high"] for b in buf]
        lows = [b["low"] for b in buf]

        # SMA slope: linear regression over last N closes
        recent = closes[-self._regime_window:]
        x = np.arange(len(recent), dtype=float)
        slope = float(np.polyfit(x, recent, 1)[0])

        # Normalize slope as percentage of mean price
        mean_price = np.mean(recent)
        sma_slope_pct = (slope / mean_price * 100) if mean_price > 0 else 0.0

        # ATR percentile: current ATR vs historical
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)

        if len(trs) >= 2:
            current_atr = np.mean(trs[-5:]) if len(trs) >= 5 else np.mean(trs)
            atr_pctile = float(np.searchsorted(np.sort(trs), current_atr) / len(trs) * 100)
        else:
            current_atr = 0
            atr_pctile = 50.0

        # Label
        trend = "uptrend" if sma_slope_pct > 0 else "downtrend"
        vol = "high_vol" if atr_pctile > 60 else "low_vol"
        label = f"{trend}_{vol}"

        return MarketRegime(
            sma_slope=round(sma_slope_pct, 4),
            atr_percentile=round(atr_pctile, 1),
            label=label,
        )

    def _compute_indicator_correlations(self) -> dict:
        """Compute pairwise correlation matrix from indicator firing vectors."""
        if len(self._indicator_firings) < 3:
            return {}

        # Get all indicator names
        all_names = set()
        for f in self._indicator_firings:
            all_names.update(f.keys())
        names = sorted(all_names)

        # Build binary matrix
        matrix = []
        for f in self._indicator_firings:
            row = [f.get(n, 0) for n in names]
            matrix.append(row)
        arr = np.array(matrix, dtype=float)

        # Compute correlation (handle constant columns)
        n_cols = arr.shape[1]
        corr = {}
        for i in range(n_cols):
            for j in range(i + 1, n_cols):
                col_i = arr[:, i]
                col_j = arr[:, j]
                # Skip if either column is constant
                if col_i.std() == 0 or col_j.std() == 0:
                    continue
                r = float(np.corrcoef(col_i, col_j)[0, 1])
                if not np.isnan(r):
                    corr[f"{names[i]} × {names[j]}"] = round(r, 3)

        return corr

    def get_results(self) -> dict:
        """Return diagnostic summary after backtest completion."""
        # Finalize any remaining post-exit trackers
        for tracker in self._post_exit:
            record = getattr(tracker, "_record_ref", None)
            if record:
                record["post_exit_max_favorable"] = round(tracker.max_favorable_after_exit, 4)
                record["post_exit_max_favorable_pct"] = round(tracker.max_favorable_after_exit_pct, 4)

        trades = self._completed_trades

        if not trades:
            return {"diagnostic_trades": 0}

        # Write JSONL
        os.makedirs(self._output_dir, exist_ok=True)
        jsonl_path = os.path.join(self._output_dir, "diagnostic_trades.jsonl")
        with open(jsonl_path, "w") as f:
            for t in trades:
                f.write(json.dumps(t, default=str) + "\n")
        logger.info("Wrote %d diagnostic trades to %s", len(trades), jsonl_path)

        # --- P&L by trigger type ---
        by_trigger = defaultdict(lambda: {"count": 0, "pnl": 0.0, "wins": 0})
        for t in trades:
            trigger = t.get("entry_trigger", "unknown")
            by_trigger[trigger]["count"] += 1
            by_trigger[trigger]["pnl"] += t["pnl"]
            if t["pnl"] > 0:
                by_trigger[trigger]["wins"] += 1

        # --- P&L by market regime ---
        by_regime = defaultdict(lambda: {"count": 0, "pnl": 0.0, "wins": 0})
        for t in trades:
            regime = t.get("entry_metadata", {}).get("regime", {}).get("label", "unknown")
            by_regime[regime]["count"] += 1
            by_regime[regime]["pnl"] += t["pnl"]
            if t["pnl"] > 0:
                by_regime[regime]["wins"] += 1

        # --- P&L by exit reason ---
        by_exit = defaultdict(lambda: {"count": 0, "pnl": 0.0})
        for t in trades:
            reason = t.get("exit_reason", "unknown")
            by_exit[reason]["count"] += 1
            by_exit[reason]["pnl"] += t["pnl"]

        # --- MFE/MAE statistics ---
        mfes = [t["mfe_pct"] for t in trades]
        maes = [t["mae_pct"] for t in trades]
        post_exit_fav = [t["post_exit_max_favorable_pct"] for t in trades]

        mfe_mae_stats = {
            "mfe_avg_pct": round(np.mean(mfes), 3) if mfes else 0,
            "mfe_median_pct": round(float(np.median(mfes)), 3) if mfes else 0,
            "mae_avg_pct": round(np.mean(maes), 3) if maes else 0,
            "mae_median_pct": round(float(np.median(maes)), 3) if maes else 0,
            "post_exit_favorable_avg_pct": round(np.mean(post_exit_fav), 3) if post_exit_fav else 0,
            "post_exit_favorable_median_pct": round(float(np.median(post_exit_fav)), 3) if post_exit_fav else 0,
        }

        # Winning trades MFE/MAE vs losing trades
        winners = [t for t in trades if t["pnl"] > 0]
        losers = [t for t in trades if t["pnl"] <= 0]

        if winners:
            mfe_mae_stats["winners_mfe_avg_pct"] = round(np.mean([t["mfe_pct"] for t in winners]), 3)
            mfe_mae_stats["winners_mae_avg_pct"] = round(np.mean([t["mae_pct"] for t in winners]), 3)
        if losers:
            mfe_mae_stats["losers_mfe_avg_pct"] = round(np.mean([t["mfe_pct"] for t in losers]), 3)
            mfe_mae_stats["losers_mae_avg_pct"] = round(np.mean([t["mae_pct"] for t in losers]), 3)

        # --- Indicator correlations ---
        correlations = self._compute_indicator_correlations()

        return {
            "diagnostic_trades": len(trades),
            "jsonl_path": jsonl_path,
            "pnl_by_trigger": dict(by_trigger),
            "pnl_by_regime": dict(by_regime),
            "pnl_by_exit_reason": dict(by_exit),
            "mfe_mae_stats": mfe_mae_stats,
            "indicator_correlations": correlations,
        }
