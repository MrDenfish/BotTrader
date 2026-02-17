"""Performance-based symbol exclusion — block buys for losing symbols.

Tracks trade outcomes per symbol using FIFO pairing of buy/sell fills.
Blocks BUY signals for symbols with poor historical performance over
a rolling window.

Exclusion criteria (any triggers a block):
- Win rate < min_win_rate (default 30%)
- Average P&L < min_avg_pnl (default -$5)
- Total P&L < min_total_pnl (default -$50)

Ported from v1's Shared_Utils/dynamic_symbol_filter.py.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.interfaces import RiskManager
from v2.core.types import (
    Direction,
    Fill,
    Portfolio,
    Position,
    RiskEvent,
    Signal,
    Side,
)

logger = logging.getLogger(__name__)


@registry.plugin("risk", "performance_filter")
class PerformanceFilter(RiskManager):
    """Block BUY signals for symbols with poor trade history.

    Tracks completed round-trips (buy→sell) from FillEvents at runtime.
    No database dependency — purely in-memory.
    """

    name = "performance_filter"

    def __init__(self, event_bus: EventBus | None = None, **kwargs: Any) -> None:
        self._bus = event_bus

        # Config
        self._window_days = int(kwargs.get("window_days", 30))
        self._min_trades = int(kwargs.get("min_trades", 3))
        self._min_win_rate = float(kwargs.get("min_win_rate", 0.30))
        self._min_avg_pnl = float(kwargs.get("min_avg_pnl", -5.0))
        self._min_total_pnl = float(kwargs.get("min_total_pnl", -50.0))

        # Per-symbol pending buys (FIFO queue): [{price, qty, timestamp}]
        self._pending_buys: dict[str, list[dict]] = {}
        # Per-symbol completed round-trips: [{pnl, timestamp}]
        self._completed_trades: dict[str, list[dict]] = {}

    def configure(self, config: Any) -> None:
        if isinstance(config, dict):
            if "window_days" in config:
                self._window_days = int(config["window_days"])
            if "min_trades" in config:
                self._min_trades = int(config["min_trades"])
            if "min_win_rate" in config:
                self._min_win_rate = float(config["min_win_rate"])
            if "min_avg_pnl" in config:
                self._min_avg_pnl = float(config["min_avg_pnl"])
            if "min_total_pnl" in config:
                self._min_total_pnl = float(config["min_total_pnl"])

    # ------------------------------------------------------------------
    # Signal validation
    # ------------------------------------------------------------------

    def check_signal(self, signal: Signal, portfolio: Portfolio) -> Signal | None:
        """Block BUY signals for symbols with poor performance."""
        if signal.direction != Direction.BUY:
            return signal  # Never block sells

        stats = self._get_stats(signal.symbol)
        if stats is None:
            return signal  # Not enough history

        if self._should_exclude(stats):
            logger.warning(
                "Performance filter BLOCKED %s BUY: win_rate=%.0f%%, "
                "avg_pnl=$%.2f, total_pnl=$%.2f (%d trades)",
                signal.symbol,
                stats["win_rate"] * 100,
                stats["avg_pnl"],
                stats["total_pnl"],
                stats["trade_count"],
            )
            if self._bus:
                self._bus.publish(RiskEvent(
                    event_type="signal_vetoed",
                    reason=f"performance_filter: {signal.symbol} excluded",
                    metadata={
                        "symbol": signal.symbol,
                        "direction": signal.direction.value,
                        **stats,
                    },
                ))
            return None

        return signal

    # ------------------------------------------------------------------
    # Fill tracking — FIFO pairing
    # ------------------------------------------------------------------

    def on_fill(self, fill: Fill, portfolio: Portfolio) -> None:
        """Track buy/sell fills for FIFO performance calculation."""
        symbol = fill.symbol
        ts = fill.timestamp.timestamp() if fill.timestamp else time.time()

        if fill.side == Side.BUY:
            if symbol not in self._pending_buys:
                self._pending_buys[symbol] = []
            self._pending_buys[symbol].append({
                "price": float(fill.price),
                "qty": float(fill.qty),
                "fee": float(fill.fee),
                "timestamp": ts,
            })

        elif fill.side == Side.SELL:
            self._match_sell(symbol, fill, ts)

    def _match_sell(self, symbol: str, fill: Fill, ts: float) -> None:
        """FIFO-match a sell fill against pending buys."""
        pending = self._pending_buys.get(symbol, [])
        if not pending:
            return

        sell_qty = float(fill.qty)
        sell_price = float(fill.price)
        sell_fee = float(fill.fee)

        while sell_qty > 0 and pending:
            buy = pending[0]
            match_qty = min(sell_qty, buy["qty"])

            # Compute P&L for this matched portion
            buy_cost = buy["price"] * match_qty
            # Proportional fees
            buy_fee = buy["fee"] * (match_qty / buy["qty"]) if buy["qty"] > 0 else 0
            sell_fee_portion = sell_fee * (match_qty / float(fill.qty)) if float(fill.qty) > 0 else 0
            sell_proceeds = sell_price * match_qty
            pnl = sell_proceeds - buy_cost - buy_fee - sell_fee_portion

            if symbol not in self._completed_trades:
                self._completed_trades[symbol] = []
            self._completed_trades[symbol].append({
                "pnl": pnl,
                "timestamp": ts,
            })

            # Update remaining quantities
            sell_qty -= match_qty
            buy["qty"] -= match_qty
            if buy["qty"] <= 1e-12:
                pending.pop(0)

    def on_position_update(self, position: Position) -> Signal | None:
        return None

    # ------------------------------------------------------------------
    # Stats computation
    # ------------------------------------------------------------------

    def _get_stats(self, symbol: str) -> dict | None:
        """Compute performance stats for symbol within rolling window.

        Returns None if insufficient trade history.
        """
        trades = self._completed_trades.get(symbol, [])
        if not trades:
            return None

        # Filter to rolling window
        cutoff = time.time() - (self._window_days * 86400)
        recent = [t for t in trades if t["timestamp"] >= cutoff]

        if len(recent) < self._min_trades:
            return None

        pnls = [t["pnl"] for t in recent]
        wins = sum(1 for p in pnls if p >= 0)
        total_pnl = sum(pnls)

        return {
            "trade_count": len(recent),
            "win_rate": wins / len(recent),
            "avg_pnl": total_pnl / len(recent),
            "total_pnl": total_pnl,
        }

    def _should_exclude(self, stats: dict) -> bool:
        """Check if stats meet any exclusion criteria."""
        if stats["win_rate"] < self._min_win_rate:
            return True
        if stats["avg_pnl"] < self._min_avg_pnl:
            return True
        if stats["total_pnl"] < self._min_total_pnl:
            return True
        return False

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        return {
            "pending_buys": dict(self._pending_buys),
            "completed_trades": dict(self._completed_trades),
        }

    def load_state(self, state: dict) -> None:
        if "pending_buys" in state:
            self._pending_buys = state["pending_buys"]
        if "completed_trades" in state:
            self._completed_trades = state["completed_trades"]
