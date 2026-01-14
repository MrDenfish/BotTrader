"""
Backtest Data Models

Represents positions, trades, and performance metrics.
"""

from decimal import Decimal
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum


class TradeType(Enum):
    """Type of trade entry"""
    ROC_MOMENTUM = "roc_momentum"
    STANDARD_SIGNAL = "standard_signal"


class ExitReason(Enum):
    """Reason for closing a position"""
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    ROC_PEAK_DROP = "roc_peak_drop"
    ROC_REVERSAL = "roc_reversal"
    MAX_HOLD_TIME = "max_hold_time"
    END_OF_BACKTEST = "end_of_backtest"


@dataclass
class Position:
    """Open trading position with peak tracking"""

    symbol: str
    side: str  # 'buy' or 'sell'
    entry_price: Decimal
    size: Decimal
    entry_time: datetime
    trade_type: TradeType

    # Peak tracking (PRICE-based, not ROC-based!)
    peak_price: Decimal = None
    price_history: list = None  # For 5-min smoothing
    peak_tracking_active: bool = False  # Activated after hitting min_profit
    breakeven_stop_active: bool = False  # Breakeven stop after +6%

    # Entry fees
    entry_fee: Decimal = Decimal("0")

    # Position tracking
    unrealized_pnl: Decimal = Decimal("0")

    def __post_init__(self):
        if self.peak_price is None:
            self.peak_price = self.entry_price
        if self.price_history is None:
            self.price_history = []

    def update_peak_with_smoothing(self, current_price: Decimal, smoothing_periods: int = 1):
        """
        Update peak price with smoothing (matches production position_monitor.py:179-211)

        Args:
            current_price: Current market price
            smoothing_periods: Number of periods for SMA (1 = no smoothing)
        """
        # Add to price history
        self.price_history.append(float(current_price))

        # Keep only last N prices for smoothing window
        max_history = max(smoothing_periods, 5)
        if len(self.price_history) > max_history:
            self.price_history = self.price_history[-max_history:]

        # Calculate smoothed price (SMA)
        if len(self.price_history) >= smoothing_periods:
            smoothed_price = Decimal(str(sum(self.price_history[-smoothing_periods:]) / smoothing_periods))
        else:
            smoothed_price = current_price

        # Update peak if smoothed price is higher
        if smoothed_price > self.peak_price:
            self.peak_price = smoothed_price

    def calculate_unrealized_pnl(self, current_price: Decimal) -> Decimal:
        """Calculate current unrealized P&L"""
        gross_pnl = (current_price - self.entry_price) * self.size
        self.unrealized_pnl = gross_pnl - self.entry_fee
        return self.unrealized_pnl

    def calculate_return_pct(self, current_price: Decimal) -> Decimal:
        """Calculate percentage return"""
        return ((current_price - self.entry_price) / self.entry_price) * Decimal("100")


@dataclass
class Trade:
    """Completed trade record"""

    symbol: str
    side: str
    trade_type: TradeType

    # Entry
    entry_price: Decimal
    entry_time: datetime
    entry_fee: Decimal

    # Exit
    exit_price: Decimal
    exit_time: datetime
    exit_fee: Decimal
    exit_reason: ExitReason

    # Size
    size: Decimal

    # P&L
    gross_pnl: Decimal
    net_pnl: Decimal
    return_pct: Decimal

    # Duration
    hold_time_hours: float

    # Peak tracking (for analysis)
    peak_price: Optional[Decimal] = None
    peak_roc: Optional[Decimal] = None

    @property
    def is_winner(self) -> bool:
        """Check if trade was profitable"""
        return self.net_pnl > 0

    @property
    def total_fees(self) -> Decimal:
        """Total fees paid"""
        return self.entry_fee + self.exit_fee


@dataclass
class BacktestResults:
    """Aggregate backtest performance metrics"""

    # Configuration
    strategy_name: str
    start_date: datetime
    end_date: datetime
    initial_capital: Decimal

    # Trade Statistics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0

    # P&L Metrics
    total_pnl: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")
    final_capital: Decimal = Decimal("0")

    # Individual trade lists
    trades: list[Trade] = field(default_factory=list)

    # Performance Ratios
    win_rate: Optional[float] = None
    profit_factor: Optional[Decimal] = None
    avg_win: Optional[Decimal] = None
    avg_loss: Optional[Decimal] = None

    # Risk Metrics
    max_drawdown: Optional[Decimal] = None
    max_drawdown_pct: Optional[Decimal] = None

    # Trade breakdown by type
    roc_trades: int = 0
    signal_trades: int = 0

    # Exit reason breakdown
    tp_exits: int = 0
    sl_exits: int = 0
    roc_exits: int = 0

    def calculate_metrics(self):
        """Calculate derived performance metrics"""
        if not self.trades:
            return

        # Win rate
        self.win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0

        # Profit factor
        gross_profit = sum(t.net_pnl for t in self.trades if t.is_winner)
        gross_loss = abs(sum(t.net_pnl for t in self.trades if not t.is_winner))
        self.profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else Decimal("0")

        # Average win/loss
        winners = [t.net_pnl for t in self.trades if t.is_winner]
        losers = [t.net_pnl for t in self.trades if not t.is_winner]

        self.avg_win = (sum(winners) / len(winners)) if winners else Decimal("0")
        self.avg_loss = (sum(losers) / len(losers)) if losers else Decimal("0")

        # Trade type breakdown
        self.roc_trades = sum(1 for t in self.trades if t.trade_type == TradeType.ROC_MOMENTUM)
        self.signal_trades = sum(1 for t in self.trades if t.trade_type == TradeType.STANDARD_SIGNAL)

        # Exit reason breakdown
        self.tp_exits = sum(1 for t in self.trades if t.exit_reason == ExitReason.TAKE_PROFIT)
        self.sl_exits = sum(1 for t in self.trades if t.exit_reason == ExitReason.STOP_LOSS)
        self.roc_exits = sum(1 for t in self.trades if t.exit_reason in [ExitReason.ROC_PEAK_DROP, ExitReason.ROC_REVERSAL])

        # Calculate drawdown
        self._calculate_drawdown()

    def _calculate_drawdown(self):
        """Calculate maximum drawdown"""
        if not self.trades:
            return

        # Build equity curve
        capital = self.initial_capital
        peak = capital
        max_dd = Decimal("0")

        for trade in self.trades:
            capital += trade.net_pnl
            if capital > peak:
                peak = capital
            drawdown = peak - capital
            if drawdown > max_dd:
                max_dd = drawdown

        self.max_drawdown = max_dd
        self.max_drawdown_pct = (max_dd / peak * Decimal("100")) if peak > 0 else Decimal("0")

    def add_trade(self, trade: Trade):
        """Add a completed trade and update metrics"""
        self.trades.append(trade)
        self.total_trades += 1

        if trade.is_winner:
            self.winning_trades += 1
        else:
            self.losing_trades += 1

        self.total_pnl += trade.net_pnl
        self.total_fees += trade.total_fees

    def get_summary(self) -> dict:
        """Get summary statistics as dictionary"""
        self.calculate_metrics()

        return {
            "strategy": self.strategy_name,
            "period": f"{self.start_date.date()} to {self.end_date.date()}",
            "initial_capital": float(self.initial_capital),
            "final_capital": float(self.final_capital),
            "total_pnl": float(self.total_pnl),
            "total_return_pct": float((self.total_pnl / self.initial_capital) * 100) if self.initial_capital > 0 else 0,
            "total_trades": self.total_trades,
            "win_rate_pct": self.win_rate,
            "profit_factor": float(self.profit_factor) if self.profit_factor else 0,
            "avg_win": float(self.avg_win) if self.avg_win else 0,
            "avg_loss": float(self.avg_loss) if self.avg_loss else 0,
            "max_drawdown": float(self.max_drawdown) if self.max_drawdown else 0,
            "max_drawdown_pct": float(self.max_drawdown_pct) if self.max_drawdown_pct else 0,
            "total_fees": float(self.total_fees),
            "roc_trades": self.roc_trades,
            "signal_trades": self.signal_trades,
            "tp_exits": self.tp_exits,
            "sl_exits": self.sl_exits,
            "roc_exits": self.roc_exits,
        }
