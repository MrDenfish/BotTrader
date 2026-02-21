"""Dynamic exit manager — trailing stops, hard stops, soft stops.

Monitors live ticker prices and generates sell signals when positions
hit configurable exit conditions. Designed to let winners run via
trailing stops while cutting losers with hard/soft stop-losses.

Exit layers (checked in priority order):
1. Hard stop — emergency exit at deep loss threshold (default -4.5%)
2. Soft stop — normal stop-loss at moderate loss threshold (default -3%)
3. Trailing stop — activates at profit threshold, tracks peak price,
   exits when price drops below peak by configurable distance

All P&L calculations are fee-aware: entry cost includes maker fee,
exit revenue deducts taker fee. Fees are fetched from the exchange API
(with caching) and fall back to config defaults.

Ported from v1's position_monitor.py multi-layer exit system.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.interfaces import RiskManager
from v2.core.types import (
    Direction,
    Fill,
    OrderType,
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

        # Fee-aware P&L: config defaults, refreshed from exchange API
        self._maker_fee = float(kwargs.get("maker_fee_pct", 0.006))
        self._taker_fee = float(kwargs.get("taker_fee_pct", 0.012))
        self._exchange = None  # Set by app.py after construction
        self._fee_fetch_time: float = 0.0
        self._fee_cache_seconds: float = 3600.0  # 1 hour
        self._fee_refresh_pending: bool = False

        # ATR-based trailing stop config
        self._trailing_mode: str = kwargs.get("trailing_mode", "fixed")  # "fixed" or "atr"
        self._atr_period: int = int(kwargs.get("atr_period", 14))
        self._atr_mult: float = float(kwargs.get("atr_mult", 2.0))
        self._trailing_min_distance_pct: float = float(kwargs.get("trailing_min_distance_pct", 0.01))
        self._trailing_max_distance_pct: float = float(kwargs.get("trailing_max_distance_pct", 0.02))

        # Peak tracking for ROC momentum trades
        self._peak_tracking_enabled: bool = kwargs.get("peak_tracking_enabled", False)
        self._peak_drawdown_pct: float = float(kwargs.get("peak_drawdown_pct", 0.05))
        self._peak_min_profit_pct: float = float(kwargs.get("peak_min_profit_pct", 0.06))
        self._peak_smoothing_prices: int = int(kwargs.get("peak_smoothing_prices", 5))
        self._peak_max_hold_minutes: int = int(kwargs.get("peak_max_hold_minutes", 1440))
        raw_triggers = kwargs.get("peak_triggers", [])
        self._peak_triggers: list[str] = list(raw_triggers) if raw_triggers else []

        # Per-symbol tracking state
        self._peak_price: dict[str, float] = {}
        self._trailing_active: dict[str, bool] = {}
        self._pending_exits: set[str] = set()
        self._last_check: dict[str, float] = {}

        # ATR trailing state: candle history and ratcheting stop prices
        self._candle_history: dict[str, deque] = {}  # symbol → deque of (high, low, close)
        self._stop_price: dict[str, float] = {}  # symbol → ATR stop price (ratchet-up only)

        # Peak tracking state: {symbol: {peak_price, price_history, entry_time, trigger_type, breakeven_activated}}
        self._peak_state: dict[str, dict] = {}

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
            if "maker_fee_pct" in config:
                self._maker_fee = float(config["maker_fee_pct"])
            if "taker_fee_pct" in config:
                self._taker_fee = float(config["taker_fee_pct"])
            if "trailing_mode" in config:
                self._trailing_mode = config["trailing_mode"]
            if "atr_period" in config:
                self._atr_period = int(config["atr_period"])
            if "atr_mult" in config:
                self._atr_mult = float(config["atr_mult"])
            if "trailing_min_distance_pct" in config:
                self._trailing_min_distance_pct = float(config["trailing_min_distance_pct"])
            if "trailing_max_distance_pct" in config:
                self._trailing_max_distance_pct = float(config["trailing_max_distance_pct"])
            if "peak_tracking_enabled" in config:
                self._peak_tracking_enabled = bool(config["peak_tracking_enabled"])
            if "peak_drawdown_pct" in config:
                self._peak_drawdown_pct = float(config["peak_drawdown_pct"])
            if "peak_min_profit_pct" in config:
                self._peak_min_profit_pct = float(config["peak_min_profit_pct"])
            if "peak_smoothing_prices" in config:
                self._peak_smoothing_prices = int(config["peak_smoothing_prices"])
            if "peak_max_hold_minutes" in config:
                self._peak_max_hold_minutes = int(config["peak_max_hold_minutes"])
            if "peak_triggers" in config:
                self._peak_triggers = list(config["peak_triggers"])

    # ------------------------------------------------------------------
    # Fee-aware P&L
    # ------------------------------------------------------------------

    def _compute_pnl(self, price: float, avg_entry: float) -> tuple[float, float]:
        """Compute fee-aware and raw P&L percentage.

        Assumes entry was maker order, exit will be taker order.
        Returns (pnl_net, pnl_raw) as fractions (not percent).
        """
        pnl_raw = (price - avg_entry) / avg_entry
        entry_cost = avg_entry * (1 + self._maker_fee)
        exit_revenue = price * (1 - self._taker_fee)
        pnl_net = (exit_revenue - entry_cost) / entry_cost
        return pnl_net, pnl_raw

    def _maybe_refresh_fees(self) -> None:
        """Trigger async fee refresh if cache is stale. Fire-and-forget."""
        if self._exchange is None or self._fee_refresh_pending:
            return
        if time.time() - self._fee_fetch_time < self._fee_cache_seconds:
            return
        self._fee_refresh_pending = True
        asyncio.ensure_future(self._refresh_fees())

    async def _refresh_fees(self) -> None:
        """Fetch fee rates from exchange API and cache them."""
        try:
            rates = await self._exchange.get_fee_rates()
            self._maker_fee = float(rates.get("maker", Decimal(str(self._maker_fee))))
            self._taker_fee = float(rates.get("taker", Decimal(str(self._taker_fee))))
            self._fee_fetch_time = time.time()
            logger.info(
                "Exit manager fees refreshed: maker=%.4f%%, taker=%.4f%%",
                self._maker_fee * 100, self._taker_fee * 100,
            )
        except Exception:
            logger.warning("Failed to refresh fees from exchange API — using cached/config values", exc_info=True)
        finally:
            self._fee_refresh_pending = False

    # ------------------------------------------------------------------
    # ATR-based trailing
    # ------------------------------------------------------------------

    def on_candle(self, candle) -> None:
        """Accumulate candle history for ATR computation (only in atr mode)."""
        if self._trailing_mode != "atr":
            return
        symbol = candle.symbol
        if symbol not in self._candle_history:
            self._candle_history[symbol] = deque(maxlen=self._atr_period + 1)
        self._candle_history[symbol].append((candle.high, candle.low, candle.close))

    def _compute_atr_distance(self, symbol: str, price: float) -> float | None:
        """Compute ATR-based trailing distance as a fraction.

        Returns the constrained distance (between min and max),
        or None if insufficient candle history.
        """
        history = self._candle_history.get(symbol)
        if not history or len(history) < 2:
            return None

        # Compute True Range for each bar (need at least 2 bars for prev close)
        true_ranges = []
        items = list(history)
        for i in range(1, len(items)):
            high, low, _ = items[i]
            prev_close = items[i - 1][2]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)

        if not true_ranges:
            return None

        # ATR = simple average of true ranges (up to atr_period)
        n = min(len(true_ranges), self._atr_period)
        atr = sum(true_ranges[-n:]) / n

        # ATR as percentage of current price
        if price <= 0:
            return None
        atr_pct = atr / price

        # Distance = atr_mult × ATR%, constrained to [min, max]
        distance = atr_pct * self._atr_mult
        distance = max(self._trailing_min_distance_pct, min(distance, self._trailing_max_distance_pct))
        return distance

    # ------------------------------------------------------------------
    # Peak tracking for ROC momentum trades
    # ------------------------------------------------------------------

    def _check_peak_tracking(self, symbol: str, price: float, pnl_pct: float, pnl_raw: float) -> bool:
        """Check peak tracking exit conditions for ROC momentum trades.

        Returns True if an exit was emitted, False otherwise.

        Exit priority (within peak tracking):
        1. Time limit (24h default) — always exits
        2. Not yet activated (pnl < min_profit) — skip, let normal stops handle it
        3. Breakeven protection (pnl <= -(fees)) — exit if profitable once then sank
        4. Peak drawdown (-5% from SMA-smoothed peak) — core exit logic
        """
        state = self._peak_state.get(symbol)
        if state is None:
            return False

        # Update SMA-smoothed peak
        state["price_history"].append(price)
        if len(state["price_history"]) > self._peak_smoothing_prices:
            state["price_history"] = state["price_history"][-self._peak_smoothing_prices:]

        smoothed = sum(state["price_history"]) / len(state["price_history"])
        if smoothed > state["peak_price"]:
            state["peak_price"] = smoothed

        # 1. Time limit check
        entry_time = state["entry_time"]
        elapsed_min = (datetime.now(timezone.utc) - entry_time).total_seconds() / 60
        if elapsed_min >= self._peak_max_hold_minutes:
            self._emit_exit(symbol, price, "peak_time_limit", {
                "pnl_pct": round(pnl_pct * 100, 2),
                "pnl_raw_pct": round(pnl_raw * 100, 2),
                "held_minutes": round(elapsed_min, 1),
                "peak_price": round(state["peak_price"], 6),
                "trigger_type": state["trigger_type"],
            })
            return True

        # 2. Activation gate: need min_profit before peak tracking kicks in.
        #    Once activated (breakeven_activated=True), skip this gate so
        #    breakeven protection and drawdown checks remain active even if
        #    P&L drops back below the activation threshold.
        if not state["breakeven_activated"]:
            if pnl_pct < self._peak_min_profit_pct:
                return False  # Not yet activated — normal stops still apply
            state["breakeven_activated"] = True
            logger.info(
                "Peak tracking ACTIVATED: %s pnl_net=+%.2f%% > +%.2f%% threshold",
                symbol, pnl_pct * 100, self._peak_min_profit_pct * 100,
            )

        # 3. Breakeven protection: exit if P&L drops to fee level
        fee_total = self._maker_fee + self._taker_fee
        if pnl_pct <= -fee_total:
            self._emit_exit(symbol, price, "peak_breakeven", {
                "pnl_pct": round(pnl_pct * 100, 2),
                "pnl_raw_pct": round(pnl_raw * 100, 2),
                "fee_total": round(fee_total * 100, 2),
                "peak_price": round(state["peak_price"], 6),
                "trigger_type": state["trigger_type"],
            })
            return True

        # 4. Peak drawdown: exit if smoothed price dropped >drawdown_pct from peak
        peak = state["peak_price"]
        if peak > 0:
            drawdown = (smoothed - peak) / peak
            if drawdown <= -self._peak_drawdown_pct:
                self._emit_exit(symbol, price, "peak_drawdown", {
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "pnl_raw_pct": round(pnl_raw * 100, 2),
                    "peak_price": round(peak, 6),
                    "smoothed_price": round(smoothed, 6),
                    "drawdown_pct": round(drawdown * 100, 2),
                    "trigger_type": state["trigger_type"],
                })
                return True

        return False

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
            avg_entry = float(pos.avg_entry_price)
            pnl_pct, pnl_raw = self._compute_pnl(signal.price, avg_entry)
            if pnl_pct >= self._signal_exit_min_pnl_pct:
                signal.metadata["exit_reason"] = "signal_exit"
                signal.metadata["pnl_pct"] = round(pnl_pct * 100, 2)
                signal.metadata["pnl_raw_pct"] = round(pnl_raw * 100, 2)
                if self._bus:
                    self._bus.publish(RiskEvent(
                        event_type="exit_triggered",
                        reason="signal_exit",
                        metadata={
                            "symbol": symbol,
                            "price": signal.price,
                            "pnl_pct": round(pnl_pct * 100, 2),
                            "pnl_raw_pct": round(pnl_raw * 100, 2),
                        },
                    ))

        return signal

    # ------------------------------------------------------------------
    # Ticker monitoring — CORE exit logic
    # ------------------------------------------------------------------

    def on_ticker(self, ticker: TickerEvent, portfolio: Portfolio) -> None:
        """Check all positions against current price for exit conditions.

        Called via TickerEvent subscription wired in app.py.
        All P&L calculations are fee-aware (maker entry, taker exit).
        """
        symbol = ticker.symbol
        price = ticker.price
        now = time.time()

        # Throttle: skip if checked recently
        last = self._last_check.get(symbol, 0.0)
        if now - last < self._check_interval_sec:
            return
        self._last_check[symbol] = now

        # Lazy fee refresh (fire-and-forget, at most every hour)
        self._maybe_refresh_fees()

        # Skip if no position or exit already pending
        position = portfolio.positions.get(symbol)
        if position is None or position.qty <= 0:
            return
        if symbol in self._pending_exits:
            return

        # Compute fee-aware P&L percentage from entry
        avg_entry = float(position.avg_entry_price)
        if avg_entry <= 0:
            return
        pnl_pct, pnl_raw = self._compute_pnl(price, avg_entry)

        # --- Layer 1: HARD STOP (highest priority, MARKET order) ---
        if pnl_pct <= -self._hard_stop_pct:
            self._emit_exit(symbol, price, "hard_stop", {
                "pnl_pct": round(pnl_pct * 100, 2),
                "pnl_raw_pct": round(pnl_raw * 100, 2),
                "threshold_pct": round(-self._hard_stop_pct * 100, 2),
                "avg_entry": avg_entry,
            }, order_type=OrderType.MARKET)
            return

        # --- Layer 2: PEAK TRACKING (ROC momentum trades) ---
        if self._peak_tracking_enabled and self._check_peak_tracking(symbol, price, pnl_pct, pnl_raw):
            return

        # --- Layer 3: SOFT STOP ---
        # Always use MARKET for soft stops — limit orders on illiquid pairs
        # get cancelled as stale, leaving the position stuck at a loss.
        if pnl_pct <= -self._soft_stop_pct:
            self._emit_exit(symbol, price, "soft_stop", {
                "pnl_pct": round(pnl_pct * 100, 2),
                "pnl_raw_pct": round(pnl_raw * 100, 2),
                "threshold_pct": round(-self._soft_stop_pct * 100, 2),
                "avg_entry": avg_entry,
            }, order_type=OrderType.MARKET)
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
                    "Trailing stop ACTIVATED: %s at $%.4f (pnl_net=+%.2f%%, pnl_raw=+%.2f%%, peak=$%.4f)",
                    symbol, price, pnl_pct * 100, pnl_raw * 100, peak,
                )
                if self._bus:
                    self._bus.publish(RiskEvent(
                        event_type="trailing_activated",
                        reason="trailing_stop",
                        metadata={"symbol": symbol, "price": price, "pnl_pct": round(pnl_pct * 100, 2)},
                    ))

        # Check trailing stop
        if self._trailing_active.get(symbol, False):
            # Determine trailing distance based on mode
            if self._trailing_mode == "atr":
                atr_dist = self._compute_atr_distance(symbol, price)
                if atr_dist is not None:
                    trailing_dist = atr_dist
                else:
                    # Fallback to fixed if no candle data yet
                    trailing_dist = self._trailing_distance_pct
                # ATR mode: compute stop price and ratchet up only
                new_stop = peak * (1 - trailing_dist)
                current_stop = self._stop_price.get(symbol)
                if current_stop is None or new_stop > current_stop:
                    self._stop_price[symbol] = new_stop
                trail_price = self._stop_price[symbol]
            else:
                # Fixed mode: simple percentage from peak
                trail_price = peak * (1 - self._trailing_distance_pct)

            if price <= trail_price:
                drawdown_from_peak = (peak - price) / peak
                self._emit_exit(symbol, price, "trailing_stop", {
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "pnl_raw_pct": round(pnl_raw * 100, 2),
                    "peak_price": peak,
                    "trail_price": round(trail_price, 4),
                    "drawdown_pct": round(drawdown_from_peak * 100, 2),
                    "avg_entry": avg_entry,
                    "trailing_mode": self._trailing_mode,
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
            self._stop_price.pop(symbol, None)  # Reset ATR stop

            # Initialize peak tracking if this is a momentum trade
            if self._peak_tracking_enabled and self._peak_triggers:
                trigger = (
                    fill.metadata.get("trigger")
                    or fill.metadata.get("signal_reason")
                    or ""
                )
                if trigger in self._peak_triggers:
                    self._peak_state[symbol] = {
                        "peak_price": float(fill.price),
                        "price_history": [],
                        "entry_time": datetime.now(timezone.utc),
                        "trigger_type": trigger,
                        "breakeven_activated": False,
                    }
                    logger.info(
                        "Peak tracking initialized: %s trigger=%s entry=$%.4f",
                        symbol, trigger, float(fill.price),
                    )

            logger.debug("Exit manager: tracking started for %s at $%.4f", symbol, float(fill.price))

        elif fill.side == Side.SELL:
            # Position closed — clean up all state
            self._peak_price.pop(symbol, None)
            self._trailing_active.pop(symbol, None)
            self._pending_exits.discard(symbol)
            self._last_check.pop(symbol, None)
            self._stop_price.pop(symbol, None)
            self._peak_state.pop(symbol, None)
            # Keep candle_history — it's useful for the next position
            logger.debug("Exit manager: tracking cleared for %s", symbol)

    def on_position_update(self, position: Position) -> Signal | None:
        """No position-driven exits from exit manager (uses ticker instead)."""
        return None

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        # Serialize peak_state: convert entry_time to ISO string
        serialized_peak = {}
        for sym, ps in self._peak_state.items():
            serialized_peak[sym] = {
                **ps,
                "entry_time": ps["entry_time"].isoformat() if ps.get("entry_time") else None,
            }
        return {
            "peak_prices": dict(self._peak_price),
            "trailing_active": dict(self._trailing_active),
            "pending_exits": list(self._pending_exits),
            "stop_prices": dict(self._stop_price),
            "peak_tracking_states": serialized_peak,
        }

    def load_state(self, state: dict) -> None:
        if "peak_prices" in state:
            self._peak_price = state["peak_prices"]
        if "trailing_active" in state:
            self._trailing_active = state["trailing_active"]
        if "pending_exits" in state:
            self._pending_exits = set(state["pending_exits"])
        if "stop_prices" in state:
            self._stop_price = state["stop_prices"]
        if "peak_tracking_states" in state:
            for sym, ps in state["peak_tracking_states"].items():
                if ps.get("entry_time") and isinstance(ps["entry_time"], str):
                    ps["entry_time"] = datetime.fromisoformat(ps["entry_time"])
                self._peak_state[sym] = ps

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit_exit(
        self,
        symbol: str,
        price: float,
        reason: str,
        metadata: dict,
        order_type: OrderType = OrderType.LIMIT,
    ) -> None:
        """Create and publish a sell signal for the given symbol."""
        self._pending_exits.add(symbol)

        signal = Signal(
            direction=Direction.SELL,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            price=price,
            reason=reason,
            order_type=order_type,
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
