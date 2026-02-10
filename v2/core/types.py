"""Shared domain types for the BotTrader v2 plugin system.

All plugins communicate using these types. No plugin-specific types
leak across boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Direction(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class Side(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    LIMIT = "limit"
    MARKET = "market"
    STOP = "stop"


class OrderStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# Market Data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Candle:
    """OHLCV bar at any timeframe."""
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    timeframe: str = "1m"


# ---------------------------------------------------------------------------
# Trading
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Signal:
    """Strategy decision output."""
    direction: Direction
    symbol: str
    timestamp: datetime
    price: float | None = None
    qty: float | None = None
    notional: float | None = None
    order_type: OrderType = OrderType.LIMIT
    reason: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Order:
    """Submitted order."""
    order_id: str
    symbol: str
    side: Side
    order_type: OrderType
    price: Decimal
    qty: Decimal
    status: OrderStatus
    timestamp: datetime
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Fill:
    """Executed trade (partial or full)."""
    fill_id: str
    order_id: str
    symbol: str
    side: Side
    price: Decimal
    qty: Decimal
    fee: Decimal
    fee_currency: str
    is_maker: bool
    timestamp: datetime
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

@dataclass
class Position:
    """Current holding for a symbol."""
    symbol: str
    qty: Decimal
    avg_entry_price: Decimal
    cost_basis: Decimal
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    entry_time: datetime | None = None


@dataclass
class Portfolio:
    """Account state snapshot."""
    cash_balance: Decimal
    positions: dict[str, Position] = field(default_factory=dict)
    total_equity: Decimal = Decimal("0")
    timestamp: datetime | None = None


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CandleEvent:
    """New candle available."""
    candle: Candle


@dataclass(frozen=True)
class SignalEvent:
    """Strategy emitted a trading signal."""
    signal: Signal
    strategy_name: str


@dataclass(frozen=True)
class OrderEvent:
    """Order submitted to exchange."""
    order: Order
    event_type: str  # "submitted", "updated", "cancelled"


@dataclass(frozen=True)
class FillEvent:
    """Order filled (fully or partially)."""
    fill: Fill


@dataclass(frozen=True)
class PositionEvent:
    """Position changed."""
    position: Position
    event_type: str  # "opened", "updated", "closed"


@dataclass(frozen=True)
class RiskEvent:
    """Risk limit triggered."""
    event_type: str  # "signal_vetoed", "circuit_breaker", "limit_reached"
    reason: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TickerEvent:
    """Live ticker update."""
    symbol: str
    price: float
    timestamp: datetime
    change_24h_pct: float | None = None
    bid: float | None = None
    ask: float | None = None


@dataclass(frozen=True)
class SymbolsUpdatedEvent:
    """Active trading pair list changed (from pair discovery)."""
    symbols: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]
    source: str = "pair_discovery"
