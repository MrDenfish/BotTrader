"""Basic risk manager — validates signals against configurable limits.

Enforces:
- Maximum exposure per symbol (USD)
- Maximum number of open positions
- Maximum daily realized loss (USD)
- Maximum single order size (USD)

In backtest mode with ``pass_through=True``, all signals pass unchecked.

Ported from MarketDataManager/position_monitor.py concepts (exit logic
stays in strategies; this plugin focuses on pre-execution signal vetting).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
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


@registry.plugin("risk", "basic")
class BasicRiskManager(RiskManager):
    """Validates signals against configurable risk limits.

    Tracks daily realized P&L from fills and vetoes signals that would
    exceed configured limits. Publishes RiskEvent when a signal is vetoed.
    """

    name = "basic"

    def __init__(self, event_bus: EventBus | None = None, **kwargs: Any) -> None:
        self._bus = event_bus
        self._max_exposure_per_symbol = Decimal(
            str(kwargs.get("max_exposure_per_symbol_usd", "inf"))
        )
        self._max_daily_loss = Decimal(
            str(kwargs.get("max_daily_loss_usd", "inf"))
        )
        self._max_positions = int(kwargs.get("max_open_positions", 100))
        self._max_order_size = Decimal(
            str(kwargs.get("max_order_size_usd", "inf"))
        )
        self._pass_through = kwargs.get("pass_through", False)
        self._hodl_symbols: set[str] = set(kwargs.get("hodl_symbols", []))

        # Daily loss tracking
        self._daily_realized_pnl = Decimal("0")
        self._pnl_date: date | None = None

        # Track fill count per symbol for exposure estimation
        self._symbol_exposure: dict[str, Decimal] = {}

    def configure(self, config: Any) -> None:
        if isinstance(config, dict):
            if "max_exposure_per_symbol_usd" in config:
                self._max_exposure_per_symbol = Decimal(
                    str(config["max_exposure_per_symbol_usd"])
                )
            if "max_daily_loss_usd" in config:
                self._max_daily_loss = Decimal(str(config["max_daily_loss_usd"]))
            if "max_open_positions" in config:
                self._max_positions = int(config["max_open_positions"])
            if "max_order_size_usd" in config:
                self._max_order_size = Decimal(str(config["max_order_size_usd"]))
            self._pass_through = config.get("pass_through", self._pass_through)
            if "hodl_symbols" in config:
                self._hodl_symbols = set(config["hodl_symbols"])

    # ------------------------------------------------------------------
    # Signal validation
    # ------------------------------------------------------------------

    def check_signal(self, signal: Signal, portfolio: Portfolio) -> Signal | None:
        """Vet signal against risk limits. Returns signal or None (vetoed)."""
        if self._pass_through:
            return signal

        # HODL gate — block all execution for HODL symbols
        if signal.symbol in self._hodl_symbols:
            self._publish_veto(
                signal, f"hodl_gate: {signal.symbol} is HODL"
            )
            return None

        # HOLD signals always pass
        if signal.direction == Direction.HOLD:
            return signal

        # Rotate daily P&L tracker
        self._rotate_daily_pnl()

        # Check daily loss limit
        if self._daily_realized_pnl < -self._max_daily_loss:
            self._publish_veto(
                signal,
                f"daily_loss_exceeded: realized={self._daily_realized_pnl}, "
                f"limit=-{self._max_daily_loss}",
            )
            return None

        if signal.direction == Direction.BUY:
            return self._check_buy(signal, portfolio)

        # SELL signals generally allowed (closing positions is risk-reducing)
        return signal

    def _check_buy(self, signal: Signal, portfolio: Portfolio) -> Signal | None:
        """Additional checks for BUY signals."""
        # Max positions
        open_positions = sum(
            1 for p in portfolio.positions.values() if p.qty > 0
        )
        if open_positions >= self._max_positions:
            self._publish_veto(
                signal,
                f"max_positions_reached: {open_positions}/{self._max_positions}",
            )
            return None

        # Max exposure per symbol
        symbol = signal.symbol
        current_exposure = self._get_exposure(symbol, portfolio)
        order_notional = self._estimate_notional(signal)

        if current_exposure + order_notional > self._max_exposure_per_symbol:
            self._publish_veto(
                signal,
                f"max_exposure_exceeded: current={current_exposure}, "
                f"order={order_notional}, limit={self._max_exposure_per_symbol}",
            )
            return None

        # Max single order size
        if order_notional > self._max_order_size:
            self._publish_veto(
                signal,
                f"order_too_large: {order_notional} > {self._max_order_size}",
            )
            return None

        return signal

    # ------------------------------------------------------------------
    # Fill tracking
    # ------------------------------------------------------------------

    def on_fill(self, fill: Fill, portfolio: Portfolio) -> None:
        """Track realized P&L from fills."""
        self._rotate_daily_pnl()

        symbol = fill.symbol
        qty = fill.qty
        price = fill.price
        fee = fill.fee

        if fill.side == Side.BUY:
            # Increase exposure
            self._symbol_exposure[symbol] = (
                self._symbol_exposure.get(symbol, Decimal("0"))
                + price * qty
            )
        elif fill.side == Side.SELL:
            # Estimate realized P&L (simplified — actual FIFO is in storage)
            position = portfolio.positions.get(symbol)
            if position and position.avg_entry_price > 0:
                entry_cost = position.avg_entry_price * qty
                exit_proceeds = price * qty - fee
                pnl = exit_proceeds - entry_cost
                self._daily_realized_pnl += pnl

            # Decrease exposure
            sold_value = price * qty
            current = self._symbol_exposure.get(symbol, Decimal("0"))
            self._symbol_exposure[symbol] = max(Decimal("0"), current - sold_value)

    def on_position_update(self, position: Position) -> Signal | None:
        """No position-driven exits from basic risk manager."""
        return None

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        return {
            "daily_realized_pnl": str(self._daily_realized_pnl),
            "pnl_date": self._pnl_date.isoformat() if self._pnl_date else None,
            "symbol_exposure": {
                k: str(v) for k, v in self._symbol_exposure.items()
            },
        }

    def load_state(self, state: dict) -> None:
        if "daily_realized_pnl" in state:
            self._daily_realized_pnl = Decimal(state["daily_realized_pnl"])
        if "pnl_date" in state and state["pnl_date"]:
            self._pnl_date = date.fromisoformat(state["pnl_date"])
        if "symbol_exposure" in state:
            self._symbol_exposure = {
                k: Decimal(v) for k, v in state["symbol_exposure"].items()
            }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _rotate_daily_pnl(self) -> None:
        """Reset daily P&L at midnight UTC."""
        today = datetime.now(timezone.utc).date()
        if self._pnl_date != today:
            if self._pnl_date is not None:
                logger.info(
                    "Daily P&L reset: previous=%s on %s",
                    self._daily_realized_pnl, self._pnl_date,
                )
            self._daily_realized_pnl = Decimal("0")
            self._pnl_date = today

    @staticmethod
    def _get_exposure(symbol: str, portfolio: Portfolio) -> Decimal:
        """Get current USD exposure for a symbol from portfolio."""
        pos = portfolio.positions.get(symbol)
        if pos and pos.qty > 0:
            return pos.qty * pos.avg_entry_price
        return Decimal("0")

    @staticmethod
    def _estimate_notional(signal: Signal) -> Decimal:
        """Estimate order notional value in USD."""
        if signal.notional is not None:
            return Decimal(str(signal.notional))
        if signal.qty is not None and signal.price is not None:
            return Decimal(str(signal.qty)) * Decimal(str(signal.price))
        return Decimal("0")

    def _publish_veto(self, signal: Signal, reason: str) -> None:
        """Log and publish a RiskEvent for a vetoed signal."""
        logger.warning(
            "Signal VETOED: %s %s — %s",
            signal.direction.value, signal.symbol, reason,
        )
        if self._bus:
            self._bus.publish(RiskEvent(
                event_type="signal_vetoed",
                reason=reason,
                metadata={
                    "symbol": signal.symbol,
                    "direction": signal.direction.value,
                    "signal_reason": signal.reason,
                },
            ))
