"""Backtest results collector — observer that tracks trades and computes metrics.

Listens to FillEvent, SignalEvent, and RiskEvent to build a complete picture
of backtest performance.  Trades are matched via FIFO (buy fills paired with
subsequent sell fills for the same symbol).

Register as ``backtest_results`` observer in YAML config.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from v2.core import registry
from v2.core.interfaces import Observer
from v2.core.types import FillEvent, RiskEvent, Side, SignalEvent

logger = logging.getLogger(__name__)


@dataclass
class ClosedTrade:
    """A completed round-trip trade (buy → sell)."""
    symbol: str
    entry_price: Decimal
    exit_price: Decimal
    qty: Decimal
    entry_time: datetime
    exit_time: datetime
    entry_fee: Decimal
    exit_fee: Decimal
    exit_reason: str = ""
    entry_metadata: dict = field(default_factory=dict)
    exit_metadata: dict = field(default_factory=dict)

    @property
    def gross_pnl(self) -> Decimal:
        return (self.exit_price - self.entry_price) * self.qty

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.entry_fee - self.exit_fee

    @property
    def total_fees(self) -> Decimal:
        return self.entry_fee + self.exit_fee

    @property
    def hold_minutes(self) -> float:
        return (self.exit_time - self.entry_time).total_seconds() / 60

    @property
    def pnl_pct(self) -> float:
        cost = self.entry_price * self.qty + self.entry_fee
        if cost == 0:
            return 0.0
        return float(self.net_pnl / cost) * 100


@dataclass
class BuyLot:
    """An unfilled buy lot in the FIFO queue."""
    symbol: str
    price: Decimal
    qty: Decimal
    fee: Decimal
    timestamp: datetime
    metadata: dict = field(default_factory=dict)


@registry.plugin("observer", "backtest_results")
class BacktestResultsCollector(Observer):
    """Collects trade-level statistics during a backtest run.

    Results are accessed via ``get_results()`` after the backtest completes.
    """

    name = "backtest_results"

    def __init__(self, event_bus=None, **kwargs: Any) -> None:
        self._bus = event_bus
        # FIFO buy queues per symbol
        self._buy_queues: dict[str, deque[BuyLot]] = defaultdict(deque)
        # Completed trades
        self._trades: list[ClosedTrade] = []
        # Signal/veto counters
        self._signal_count: dict[str, int] = defaultdict(int)  # buy/sell/hold
        self._veto_count: int = 0
        self._exit_reasons: dict[str, int] = defaultdict(int)
        # Fill counters
        self._buy_fills: int = 0
        self._sell_fills: int = 0
        # Equity curve for drawdown
        self._initial_balance: Decimal = Decimal(str(kwargs.get("initial_balance_usd", 10_000)))
        self._equity_high: Decimal = self._initial_balance
        self._max_drawdown_pct: float = 0.0

    def on_event(self, event: Any) -> None:
        if isinstance(event, FillEvent):
            self._on_fill(event)
        elif isinstance(event, SignalEvent):
            direction = event.signal.direction.value
            self._signal_count[direction] += 1
        elif isinstance(event, RiskEvent):
            if event.event_type == "signal_vetoed":
                self._veto_count += 1
            elif event.event_type == "exit_triggered":
                reason = event.reason
                self._exit_reasons[reason] += 1

    def _on_fill(self, event: FillEvent) -> None:
        fill = event.fill
        if fill.side == Side.BUY:
            self._buy_fills += 1
            self._buy_queues[fill.symbol].append(BuyLot(
                symbol=fill.symbol,
                price=fill.price,
                qty=fill.qty,
                fee=fill.fee,
                timestamp=fill.timestamp,
                metadata=dict(fill.metadata) if fill.metadata else {},
            ))
        elif fill.side == Side.SELL:
            self._sell_fills += 1
            self._match_sell(fill)

    def _match_sell(self, fill) -> None:
        """FIFO match a sell fill against buy lots."""
        symbol = fill.symbol
        queue = self._buy_queues.get(symbol)
        if not queue:
            logger.warning("Backtest sell with no buy lot: %s", symbol)
            return

        remaining_qty = fill.qty
        while remaining_qty > 0 and queue:
            lot = queue[0]
            match_qty = min(remaining_qty, lot.qty)

            # Pro-rate fees
            entry_fee_share = lot.fee * (match_qty / lot.qty) if lot.qty > 0 else Decimal("0")
            exit_fee_share = fill.fee * (match_qty / fill.qty) if fill.qty > 0 else Decimal("0")

            trade = ClosedTrade(
                symbol=symbol,
                entry_price=lot.price,
                exit_price=fill.price,
                qty=match_qty,
                entry_time=lot.timestamp,
                exit_time=fill.timestamp,
                entry_fee=entry_fee_share,
                exit_fee=exit_fee_share,
                exit_reason=fill.metadata.get("signal_reason", ""),
                entry_metadata=dict(lot.metadata) if lot.metadata else {},
                exit_metadata=dict(fill.metadata) if fill.metadata else {},
            )
            self._trades.append(trade)

            # Update equity tracking for drawdown
            self._update_drawdown(trade)

            lot.qty -= match_qty
            lot.fee -= entry_fee_share
            if lot.qty <= Decimal("1e-12"):
                queue.popleft()
            remaining_qty -= match_qty

    def _update_drawdown(self, trade: ClosedTrade) -> None:
        """Track running equity and max drawdown."""
        # Running realized equity
        realized = self._initial_balance + sum(t.net_pnl for t in self._trades)
        if realized > self._equity_high:
            self._equity_high = realized
        if self._equity_high > 0:
            dd = float((self._equity_high - realized) / self._equity_high)
            if dd > self._max_drawdown_pct:
                self._max_drawdown_pct = dd

    def get_results(self) -> dict:
        """Return backtest statistics dict."""
        trades = self._trades
        total = len(trades)

        if total == 0:
            return {
                "trades": 0,
                "gross_pnl": 0.0,
                "fees": 0.0,
                "net_pnl": 0.0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "max_drawdown_pct": 0.0,
                "signals_buy": self._signal_count.get("buy", 0),
                "signals_sell": self._signal_count.get("sell", 0),
                "buy_fills": self._buy_fills,
                "sell_fills": self._sell_fills,
                "vetoes": self._veto_count,
                "exit_reasons": dict(self._exit_reasons),
                "open_positions": sum(len(q) for q in self._buy_queues.values()),
                "trade_log": [],
            }

        gross_pnl = sum(t.gross_pnl for t in trades)
        total_fees = sum(t.total_fees for t in trades)
        net_pnl = sum(t.net_pnl for t in trades)

        winners = [t for t in trades if t.net_pnl > 0]
        losers = [t for t in trades if t.net_pnl <= 0]

        gross_wins = sum(t.net_pnl for t in winners)
        gross_losses = abs(sum(t.net_pnl for t in losers))
        profit_factor = float(gross_wins / gross_losses) if gross_losses > 0 else float("inf")

        avg_win = float(sum(t.net_pnl for t in winners) / len(winners)) if winners else 0.0
        avg_loss = float(sum(t.net_pnl for t in losers) / len(losers)) if losers else 0.0

        avg_hold = sum(t.hold_minutes for t in trades) / total

        # Trade log for detailed output
        trade_log = []
        for t in trades:
            entry = {
                "symbol": t.symbol,
                "entry_price": float(t.entry_price),
                "exit_price": float(t.exit_price),
                "qty": float(t.qty),
                "net_pnl": float(t.net_pnl),
                "pnl_pct": t.pnl_pct,
                "fees": float(t.total_fees),
                "hold_min": round(t.hold_minutes, 1),
                "exit_reason": t.exit_reason,
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat(),
                "entry_trigger": t.entry_metadata.get("trigger", ""),
                "entry_score": t.entry_metadata.get("buy_score", 0.0),
            }
            # Include indicator snapshot if present
            snapshot = t.entry_metadata.get("indicator_snapshot")
            if snapshot:
                fired = [k for k, v in snapshot.items() if v.get("decision") == 1]
                entry["entry_indicators_fired"] = fired
            trade_log.append(entry)

        return {
            "trades": total,
            "winners": len(winners),
            "losers": len(losers),
            "gross_pnl": float(gross_pnl),
            "fees": float(total_fees),
            "net_pnl": float(net_pnl),
            "win_rate": len(winners) / total,
            "profit_factor": profit_factor,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "avg_hold_minutes": round(avg_hold, 1),
            "max_drawdown_pct": round(self._max_drawdown_pct * 100, 2),
            "signals_buy": self._signal_count.get("buy", 0),
            "signals_sell": self._signal_count.get("sell", 0),
            "buy_fills": self._buy_fills,
            "sell_fills": self._sell_fills,
            "vetoes": self._veto_count,
            "exit_reasons": dict(self._exit_reasons),
            "open_positions": sum(len(q) for q in self._buy_queues.values()),
            "trade_log": trade_log,
        }

    @property
    def trades(self) -> list[ClosedTrade]:
        return self._trades
