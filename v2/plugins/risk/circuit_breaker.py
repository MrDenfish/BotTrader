"""Circuit breaker risk manager — emergency halt on anomalies.

Monitors for dangerous conditions and halts all trading when triggered:
- Rapid consecutive losses (N losses within M minutes)
- Consecutive order rejections
- Large single loss exceeding threshold

When tripped, the circuit breaker vetoes ALL signals until manually
reset or the cooldown period expires.
"""

from __future__ import annotations

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
    Fill,
    Portfolio,
    Position,
    RiskEvent,
    Signal,
    Side,
)

logger = logging.getLogger(__name__)


@registry.plugin("risk", "circuit_breaker")
class CircuitBreakerRiskManager(RiskManager):
    """Emergency halt when anomalous conditions are detected.

    Tracks:
    - Recent losses (rolling window)
    - Consecutive order rejections
    - Single large losses

    When tripped, vetoes ALL signals and publishes a RiskEvent.
    Auto-resets after cooldown_minutes, or can be manually reset.
    """

    name = "circuit_breaker"

    def __init__(self, event_bus: EventBus | None = None, **kwargs: Any) -> None:
        self._bus = event_bus

        # Configurable thresholds
        self._max_losses_in_window = int(kwargs.get("max_losses_in_window", 5))
        self._loss_window_minutes = int(kwargs.get("loss_window_minutes", 30))
        self._max_consecutive_rejections = int(
            kwargs.get("max_consecutive_rejections", 10)
        )
        self._large_loss_threshold = Decimal(
            str(kwargs.get("large_loss_threshold_usd", "500"))
        )
        self._cooldown_minutes = int(kwargs.get("cooldown_minutes", 60))

        # State
        self._tripped = False
        self._trip_time: float | None = None
        self._trip_reason: str = ""

        # Rolling loss window: (timestamp, pnl_usd)
        self._recent_losses: deque[tuple[float, Decimal]] = deque()
        self._consecutive_rejections = 0

    def configure(self, config: Any) -> None:
        if isinstance(config, dict):
            if "max_losses_in_window" in config:
                self._max_losses_in_window = int(config["max_losses_in_window"])
            if "loss_window_minutes" in config:
                self._loss_window_minutes = int(config["loss_window_minutes"])
            if "max_consecutive_rejections" in config:
                self._max_consecutive_rejections = int(
                    config["max_consecutive_rejections"]
                )
            if "large_loss_threshold_usd" in config:
                self._large_loss_threshold = Decimal(
                    str(config["large_loss_threshold_usd"])
                )
            if "cooldown_minutes" in config:
                self._cooldown_minutes = int(config["cooldown_minutes"])

    # ------------------------------------------------------------------
    # Signal validation
    # ------------------------------------------------------------------

    def check_signal(self, signal: Signal, portfolio: Portfolio) -> Signal | None:
        """Veto all signals when circuit breaker is tripped."""
        # Check for auto-reset
        if self._tripped:
            if self._should_auto_reset():
                self.reset()
            else:
                logger.warning(
                    "Circuit breaker ACTIVE — vetoing %s %s (reason: %s)",
                    signal.direction.value, signal.symbol, self._trip_reason,
                )
                if self._bus:
                    self._bus.publish(RiskEvent(
                        event_type="signal_vetoed",
                        reason=f"circuit_breaker_active: {self._trip_reason}",
                        metadata={"symbol": signal.symbol, "direction": signal.direction.value},
                    ))
                return None

        return signal

    # ------------------------------------------------------------------
    # Fill / rejection tracking
    # ------------------------------------------------------------------

    def on_fill(self, fill: Fill, portfolio: Portfolio) -> None:
        """Track losses from fills."""
        if self._tripped:
            return

        # Reset rejection counter on successful fill
        self._consecutive_rejections = 0

        # Only track sell fills for loss detection
        if fill.side != Side.SELL:
            return

        position = portfolio.positions.get(fill.symbol)
        if not position or position.avg_entry_price <= 0:
            return

        # Estimate P&L
        entry_cost = position.avg_entry_price * fill.qty
        exit_proceeds = fill.price * fill.qty - fill.fee
        pnl = exit_proceeds - entry_cost

        if pnl < 0:
            now = time.time()
            self._recent_losses.append((now, pnl))
            self._prune_old_losses()

            # Check: large single loss
            if abs(pnl) >= self._large_loss_threshold:
                self._trip(
                    f"large_single_loss: {pnl} on {fill.symbol} "
                    f"(threshold: -{self._large_loss_threshold})"
                )
                return

            # Check: too many losses in window
            if len(self._recent_losses) >= self._max_losses_in_window:
                self._trip(
                    f"rapid_losses: {len(self._recent_losses)} losses in "
                    f"{self._loss_window_minutes}min "
                    f"(threshold: {self._max_losses_in_window})"
                )

    def on_rejection(self) -> None:
        """Call when an order is rejected. Tracks consecutive rejections."""
        self._consecutive_rejections += 1
        if self._consecutive_rejections >= self._max_consecutive_rejections:
            self._trip(
                f"consecutive_rejections: {self._consecutive_rejections} "
                f"(threshold: {self._max_consecutive_rejections})"
            )

    def on_position_update(self, position: Position) -> Signal | None:
        return None

    # ------------------------------------------------------------------
    # Trip / Reset
    # ------------------------------------------------------------------

    def _trip(self, reason: str) -> None:
        """Activate the circuit breaker."""
        self._tripped = True
        self._trip_time = time.time()
        self._trip_reason = reason

        logger.critical("CIRCUIT BREAKER TRIPPED: %s", reason)

        if self._bus:
            self._bus.publish(RiskEvent(
                event_type="circuit_breaker",
                reason=reason,
                metadata={
                    "cooldown_minutes": self._cooldown_minutes,
                    "recent_loss_count": len(self._recent_losses),
                    "consecutive_rejections": self._consecutive_rejections,
                },
            ))

    def reset(self) -> None:
        """Manually or automatically reset the circuit breaker."""
        if self._tripped:
            logger.info(
                "Circuit breaker RESET (was tripped: %s)", self._trip_reason,
            )
        self._tripped = False
        self._trip_time = None
        self._trip_reason = ""
        self._recent_losses.clear()
        self._consecutive_rejections = 0

    def _should_auto_reset(self) -> bool:
        """Check if cooldown period has elapsed."""
        if self._trip_time is None:
            return False
        elapsed_minutes = (time.time() - self._trip_time) / 60.0
        return elapsed_minutes >= self._cooldown_minutes

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _prune_old_losses(self) -> None:
        """Remove losses outside the rolling window."""
        cutoff = time.time() - (self._loss_window_minutes * 60)
        while self._recent_losses and self._recent_losses[0][0] < cutoff:
            self._recent_losses.popleft()

    @property
    def is_tripped(self) -> bool:
        return self._tripped

    def get_state(self) -> dict:
        return {
            "tripped": self._tripped,
            "trip_time": self._trip_time,
            "trip_reason": self._trip_reason,
            "consecutive_rejections": self._consecutive_rejections,
        }

    def load_state(self, state: dict) -> None:
        self._tripped = state.get("tripped", False)
        self._trip_time = state.get("trip_time")
        self._trip_reason = state.get("trip_reason", "")
        self._consecutive_rejections = state.get("consecutive_rejections", 0)
