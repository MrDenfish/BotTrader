"""
Core types for the strategy plugin architecture.

These dataclasses define the contract between strategies and engines:
- OHLCV: Standard bar data
- StrategySignal: What a strategy emits (BUY/SELL/HOLD + metadata)
- FillNotification: Engine tells strategy about a fill
- StrategyMetrics: Standard performance metrics
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Any


class SignalDirection(Enum):
    """Direction of a strategy signal."""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class OHLCV:
    """Standard bar data passed to strategies."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_series(cls, series) -> 'OHLCV':
        """Create from a pandas Series (with DatetimeIndex)."""
        return cls(
            timestamp=series.name,
            open=series['open'],
            high=series['high'],
            low=series['low'],
            close=series['close'],
            volume=series.get('volume', 0.0),
        )


@dataclass
class StrategySignal:
    """
    What a strategy emits from evaluate() or on_fill().

    A single signal per bar per symbol. Contains the decision and
    all metadata needed for the engine to act on it.
    """
    direction: SignalDirection
    symbol: str
    timestamp: datetime

    # Order parameters (for BUY/SELL)
    price: Optional[float] = None       # Limit price (None = market)
    qty: Optional[float] = None         # Quantity in base currency
    notional: Optional[float] = None    # Notional in quote currency

    # Order type hints
    post_only: bool = True              # Maker-only order
    is_stop: bool = False               # Stop/taker exit

    # Strategy-specific metadata
    reason: str = ""                    # e.g. "RETEST_FILL", "TP1", "TRAIL_HIT"
    metadata: dict = field(default_factory=dict)


@dataclass
class FillNotification:
    """
    Engine notifies strategy that an order was filled.

    In backtest: simulated fill from fill_simulator.
    In production: real fill from exchange websocket.
    """
    symbol: str
    fill_time: datetime
    fill_price: float
    fill_qty: float
    side: str                           # "BUY" or "SELL"
    fee: float = 0.0
    fee_rate: float = 0.0              # maker or taker rate applied
    is_maker: bool = True
    order_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class StrategyMetrics:
    """
    Standard performance metrics returned by get_metrics().

    Strategies populate what they can; engines may add more.
    """
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0

    gross_pnl: float = 0.0
    total_fees: float = 0.0
    net_pnl: float = 0.0

    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0

    # Strategy-specific extras
    extras: dict = field(default_factory=dict)
