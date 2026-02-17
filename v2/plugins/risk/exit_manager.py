"""Dynamic exit manager — trailing stops, hard stops, soft stops.

Monitors live ticker prices and generates sell signals when positions
hit configurable exit conditions. Designed to let winners run via
trailing stops while cutting losers with hard/soft stop-losses.

Exit layers (checked in priority order):
1. Hard stop — emergency exit at deep loss threshold (default -4.5%)
2. Soft stop — normal stop-loss at moderate loss threshold (default -3%)
3. Trailing stop — activates at profit threshold, tracks peak price,
   exits when price drops below peak by configurable distance

Ported from v1's position_monitor.py multi-layer exit system.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
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
    SignalEvent,
    Side,
    TickerEvent,
)

logger = logging.getLogger(__name__)


@registry.plugin("risk", "exit_manager")
class ExitManager(RiskManager):
    """Dynamic exit manager — monitors prices and triggers stop/trail exits.

    Subscribes to TickerEvent (wired via app.py hasattr check).
    Publishes SignalEvent directly on the bus for exits, which then
    flows through the normal risk chain → execution pipeline.
    """

    name = "exit_manager"

    def __init__(self, event_bus: EventBus | None = None, **kwargs: Any) -> None:
        self._bus = event_bus

        # Configurable thresholds
        self._hard_stop_pct = float(kwargs.get("hard_stop_pct", 0.045))
        self._soft_stop_pct = float(kwargs.get("soft_stop_pct", 0.03))
        self._trailing_activation_pct = float(kwargs.get("trailing_activation_pct", 0.03))
        self._trailing_distance_pct = float(kwargs.get("trailing_distance_pct", 0.03))
        self._check_interval_sec = float(kwargs.get("check_interval_sec", 5.0))

        # Signal-based exit config
        self._signal_exit_enabled = bool(kwargs.get("signal_exit_enabled", True))
        self._signal_exit_min_pnl_pct = float(kwargs.get("signal_exit_min_pnl_pct", 0.0))

        # Per-symbol tracking state
        self._peak_price: dict[str, float] = {}
        self._trailing_active: dict[str, bool] = {}
        self._pending_exits: set[str] = set()
        self._last_check: dict[str, float] = {}

    def configure(self, config: Any) -> None:
        if isinstance(config, dict):
            if "hard_stop_pct" in config:
                self._hard_stop_pct = float(config["hard_stop_pct"])
            if "soft_stop_pct" in config:
                self._soft_stop_pct = float(config["soft_stop_pct"])
            if "trailing_activation_pct" in config:
                self._trailing_activation_pct = float(config["trailing_activation_pct"])
            if "trailing_distance_pct" in config:
                self._trailing_distance_pct = float(config["trailing_distance_pct"])
            if "check_interval_sec" in config:
                self._check_interval_sec = float(config["check_interval_sec"])
            if "signal_exit_enabled" in config:
                self._signal_exit_enabled = bool(config["signal_exit_enabled"])
            if "signal_exit_min_pnl_pct" in config:
                self._signal_exit_min_pnl_pct = float(config["signal_exit_min_pnl_pct"])

    # ------------------------------------------------------------------
    # Signal validation — signal-based exits
    # ------------------------------------------------------------------

    def check_signal(self, signal: Signal, portfolio: Portfolio) -> Signal | None:
        """Signal-based exits: approve strategy SELL when position is profitable.

        If trailing stop is active for the symbol, block the strategy sell
        (trailing has priority). Otherwise, tag profitable sells with
        exit_reason metadata.
        """
        if not self._signal_exit_enabled or signal.direction != Direction.SELL:
            return signal

        symbol = signal.symbol

        # If trailing stop is active, let trailing handle it
        if self._trailing_active.get(symbol, False):
            logger.debug("Signal-based sell blocked for %s: trailing stop active", symbol)
            return None

        # Tag profitable sells with signal_exit metadata
        pos = portfolio.positions.get(symbol)
        if pos and pos.qty > 0 and float(pos.avg_entry_price) > 0 and signal.price:
            pnl_pct = (signal.price - float(pos.avg_entry_price)) / float(pos.avg_entry_price)
            if pnl_pct >= self._signal_exit_min_pnl_pct:
                signal.metadata["exit_reason"] = "signal_exit"
                signal.metadata["pnl_pct"] = round(pnl_pct * 100, 2)
                if self._bus:
                    self._bus.publish(RiskEvent(
                        event_type="exit_triggered",
                        reason="signal_exit",
                        metadata={
                            "symbol": symbol,
                            "price": signal.price,
                            "pnl_pct": round(pnl_pct * 100, 2),
                        },
                    ))

        return signal

    # ------------------------------------------------------------------
    # Ticker monitoring — CORE exit logic
    # ------------------------------------------------------------------

    def on_ticker(self, ticker: TickerEvent, portfolio: Portfolio) -> None:
        """Check all positions against current price for exit conditions.

        Called via TickerEvent subscription wired in app.py.
        """
        symbol = ticker.symbol
        price = ticker.price
        now = time.time()

        # Throttle: skip if checked recently
        last = self._last_check.get(symbol, 0.0)
        if now - last < self._check_interval_sec:
            return
        self._last_check[symbol] = now

        # Skip if no position or exit already pending
        position = portfolio.positions.get(symbol)
        if position is None or position.qty <= 0:
            return
        if symbol in self._pending_exits:
            return

        # Compute P&L percentage from entry
        avg_entry = float(position.avg_entry_price)
        if avg_entry <= 0:
            return
        pnl_pct = (price - avg_entry) / avg_entry

        # --- Layer 1: HARD STOP (highest priority) ---
        if pnl_pct <= -self._hard_stop_pct:
            self._emit_exit(symbol, price, "hard_stop", {
                "pnl_pct": round(pnl_pct * 100, 2),
                "threshold_pct": round(-self._hard_stop_pct * 100, 2),
                "avg_entry": avg_entry,
            })
            return

        # --- Layer 2: SOFT STOP ---
        if pnl_pct <= -self._soft_stop_pct:
            self._emit_exit(symbol, price, "soft_stop", {
                "pnl_pct": round(pnl_pct * 100, 2),
                "threshold_pct": round(-self._soft_stop_pct * 100, 2),
                "avg_entry": avg_entry,
            })
            return

        # --- Layer 3: TRAILING STOP ---
        # Update peak price (only ratchets up)
        peak = self._peak_price.get(symbol, avg_entry)
        if price > peak:
            peak = price
            self._peak_price[symbol] = peak

        # Activate trailing if profit threshold reached
        if not self._trailing_active.get(symbol, False):
            if pnl_pct >= self._trailing_activation_pct:
                self._trailing_active[symbol] = True
                logger.info(
                    "Trailing stop ACTIVATED: %s at $%.4f (pnl=+%.2f%%, peak=$%.4f)",
                    symbol, price, pnl_pct * 100, peak,
                )
                if self._bus:
                    self._bus.publish(RiskEvent(
                        event_type="trailing_activated",
                        reason="trailing_stop",
                        metadata={"symbol": symbol, "price": price, "pnl_pct": round(pnl_pct * 100, 2)},
                    ))

        # Check trailing stop
        if self._trailing_active.get(symbol, False):
            trail_price = peak * (1 - self._trailing_distance_pct)
            if price <= trail_price:
                drawdown_from_peak = (peak - price) / peak
                self._emit_exit(symbol, price, "trailing_stop", {
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "peak_price": peak,
                    "trail_price": round(trail_price, 4),
                    "drawdown_pct": round(drawdown_from_peak * 100, 2),
                    "avg_entry": avg_entry,
                })
                return

    # ------------------------------------------------------------------
    # Fill tracking — reset/clear state
    # ------------------------------------------------------------------

    def on_fill(self, fill: Fill, portfolio: Portfolio) -> None:
        """Reset tracking on buys, clear on sells."""
        symbol = fill.symbol

        if fill.side == Side.BUY:
            # New position — initialize tracking
            self._peak_price[symbol] = float(fill.price)
            self._trailing_active[symbol] = False
            self._pending_exits.discard(symbol)
            logger.debug("Exit manager: tracking started for %s at $%.4f", symbol, float(fill.price))

        elif fill.side == Side.SELL:
            # Position closed — clean up all state
            self._peak_price.pop(symbol, None)
            self._trailing_active.pop(symbol, None)
            self._pending_exits.discard(symbol)
            self._last_check.pop(symbol, None)
            logger.debug("Exit manager: tracking cleared for %s", symbol)

    def on_position_update(self, position: Position) -> Signal | None:
        """No position-driven exits from exit manager (uses ticker instead)."""
        return None

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        return {
            "peak_prices": dict(self._peak_price),
            "trailing_active": dict(self._trailing_active),
            "pending_exits": list(self._pending_exits),
        }

    def load_state(self, state: dict) -> None:
        if "peak_prices" in state:
            self._peak_price = state["peak_prices"]
        if "trailing_active" in state:
            self._trailing_active = state["trailing_active"]
        if "pending_exits" in state:
            self._pending_exits = set(state["pending_exits"])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit_exit(self, symbol: str, price: float, reason: str, metadata: dict) -> None:
        """Create and publish a sell signal for the given symbol."""
        self._pending_exits.add(symbol)

        signal = Signal(
            direction=Direction.SELL,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            price=price,
            reason=reason,
            metadata=metadata,
        )

        logger.warning(
            "EXIT SIGNAL: %s %s at $%.4f — %s (pnl=%.2f%%)",
            reason.upper(), symbol, price, reason, metadata.get("pnl_pct", 0),
        )

        if self._bus:
            self._bus.publish(RiskEvent(
                event_type="exit_triggered",
                reason=reason,
                metadata={"symbol": symbol, "price": price, **metadata},
            ))
            self._bus.publish(SignalEvent(
                signal=signal,
                strategy_name="exit_manager",
            ))
