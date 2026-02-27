"""Portfolio value tracker — shadow portfolio from events for watermark tracking."""

from __future__ import annotations

import time
from decimal import Decimal

from v2.core.types import Side

from ..models import PortfolioSnapshot


class PortfolioTracker:
    """Tracks portfolio value in-memory from FillEvents and TickerEvents.

    Maintains a shadow view of cash + positions to compute starting/ending
    balance and high/low watermarks for each reporting period.
    """

    def __init__(self, initial_balance_usd: float = 10_000.0) -> None:
        self._initial_balance = Decimal(str(initial_balance_usd))
        self._cash = self._initial_balance
        self._positions: dict[str, Decimal] = {}       # symbol -> qty
        self._avg_entries: dict[str, Decimal] = {}      # symbol -> avg entry
        self._last_prices: dict[str, float] = {}        # symbol -> last ticker price

        # Period tracking
        self._period_start_value: float = initial_balance_usd
        self._period_high: float = initial_balance_usd
        self._period_low: float = initial_balance_usd

        # Throttle watermark updates
        self._last_watermark_check: float = 0.0

    def on_fill(self, symbol: str, side: Side, price: Decimal, qty: Decimal, fee: Decimal) -> None:
        """Update shadow portfolio from a fill."""
        notional = price * qty

        if side == Side.BUY:
            self._cash -= notional + fee
            # Update position with simple average
            existing_qty = self._positions.get(symbol, Decimal("0"))
            existing_cost = self._avg_entries.get(symbol, Decimal("0")) * existing_qty
            new_qty = existing_qty + qty
            if new_qty > 0:
                self._avg_entries[symbol] = (existing_cost + notional) / new_qty
            self._positions[symbol] = new_qty
            self._last_prices[symbol] = float(price)
        elif side == Side.SELL:
            self._cash += notional - fee
            existing_qty = self._positions.get(symbol, Decimal("0"))
            new_qty = existing_qty - qty
            if new_qty <= Decimal("1e-12"):
                self._positions.pop(symbol, None)
                self._avg_entries.pop(symbol, None)
            else:
                self._positions[symbol] = new_qty

        self._update_watermarks()

    def on_ticker(self, symbol: str, price: float) -> None:
        """Update last-known price for mark-to-market."""
        self._last_prices[symbol] = price

        # Throttle watermark updates to every 60 seconds
        now = time.monotonic()
        if now - self._last_watermark_check > 60.0:
            self._update_watermarks()
            self._last_watermark_check = now

    def replay_fills(self, rows: list) -> None:
        """Replay historical fills to hydrate cash + positions.

        Called once at startup with all prior v2_fills so the tracker
        reflects the true portfolio state rather than a flat initial balance.
        Resets to initial balance first to avoid double-counting any fills
        that arrived via on_fill() before hydration.
        """
        self._cash = self._initial_balance
        self._positions.clear()
        self._avg_entries.clear()

        for row in rows:
            symbol = row["symbol"]
            side_str = row["side"].upper()
            price = Decimal(str(row["price"]))
            qty = Decimal(str(row["qty"]))
            fee = Decimal(str(row["fee"]))
            notional = price * qty

            if side_str == "BUY":
                self._cash -= notional + fee
                existing_qty = self._positions.get(symbol, Decimal("0"))
                existing_cost = self._avg_entries.get(symbol, Decimal("0")) * existing_qty
                new_qty = existing_qty + qty
                if new_qty > 0:
                    self._avg_entries[symbol] = (existing_cost + notional) / new_qty
                self._positions[symbol] = new_qty
                self._last_prices[symbol] = float(price)
            elif side_str == "SELL":
                existing_qty = self._positions.get(symbol, Decimal("0"))
                # Cap sell qty at available position to ignore phantom sells
                # (from stale v2_positions restores on prior container restarts)
                sell_qty = min(qty, existing_qty) if existing_qty > 0 else Decimal("0")
                if sell_qty <= 0:
                    continue  # Skip phantom sell — no position to sell
                sell_notional = price * sell_qty
                self._cash += sell_notional - fee
                new_qty = existing_qty - sell_qty
                if new_qty <= Decimal("1e-12"):
                    self._positions.pop(symbol, None)
                    self._avg_entries.pop(symbol, None)
                else:
                    self._positions[symbol] = new_qty

        # Reset watermarks to current value after replay
        current = self._total_value()
        self._period_start_value = current
        self._period_high = current
        self._period_low = current

    def start_new_period(self) -> None:
        """Snapshot starting value and reset watermarks for new period."""
        current = self._total_value()
        self._period_start_value = current
        self._period_high = current
        self._period_low = current

    def snapshot(self) -> PortfolioSnapshot:
        """Return current portfolio state for the report."""
        current = self._total_value()
        cash = float(self._cash)
        positions_value = current - cash
        return PortfolioSnapshot(
            starting_value=self._period_start_value,
            ending_value=current,
            high_watermark=self._period_high,
            low_watermark=self._period_low,
            cash_balance=cash,
            positions_value=positions_value,
        )

    def _total_value(self) -> float:
        """Cash + mark-to-market value of all positions."""
        mtm = sum(
            float(qty) * self._last_prices.get(sym, float(self._avg_entries.get(sym, Decimal("0"))))
            for sym, qty in self._positions.items()
            if qty > 0
        )
        return float(self._cash) + mtm

    def _update_watermarks(self) -> None:
        """Update period high/low watermarks."""
        current = self._total_value()
        if current > self._period_high:
            self._period_high = current
        if current < self._period_low:
            self._period_low = current
