"""Data models for the v2 daily report plugin."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass
class TradeStats:
    """Aggregated fill statistics for the reporting period."""

    buy_count: int = 0
    sell_count: int = 0
    buy_volume_usd: Decimal = Decimal("0")
    sell_volume_usd: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")
    symbols_traded: list[str] = field(default_factory=list)


@dataclass
class PnLSummary:
    """FIFO-based realized P&L for the reporting period."""

    realized_pnl: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    avg_win: Decimal = Decimal("0")
    avg_loss: Decimal = Decimal("0")
    best_trade: tuple[str, Decimal] | None = None   # (symbol, pnl)
    worst_trade: tuple[str, Decimal] | None = None   # (symbol, pnl)
    by_symbol: dict[str, Decimal] = field(default_factory=dict)
    fills_by_symbol: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass
class PositionSnapshot:
    """Current open position for a single symbol."""

    symbol: str
    qty: float
    avg_entry: float
    cost_basis: float
    current_price: float | None = None
    unrealized_pnl: float | None = None


@dataclass
class SignalStats:
    """Signal generation statistics for the reporting period."""

    total: int = 0
    buy_count: int = 0
    sell_count: int = 0
    by_symbol: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass
class RiskStats:
    """Risk event statistics for the reporting period."""

    vetoes: int = 0
    performance_filter_vetoes: int = 0
    circuit_breaker_trips: int = 0
    rejections: int = 0
    stale_cancellations: int = 0
    events: list[str] = field(default_factory=list)


@dataclass
class ExitEventDetail:
    """Single exit manager event for the report."""

    timestamp: datetime
    symbol: str
    reason: str          # "hard_stop", "soft_stop", "trailing_stop"
    price: float
    pnl_pct: float
    peak_price: float | None = None


@dataclass
class ExitManagerStats:
    """Exit manager activity for the reporting period."""

    hard_stops: int = 0
    soft_stops: int = 0
    trailing_stops: int = 0
    stale_exits: int = 0
    signal_exits: int = 0
    trailing_activations: int = 0
    total_exits: int = 0
    events: list[ExitEventDetail] = field(default_factory=list)


@dataclass
class TradeLogEntry:
    """Individual fill for the trade log section."""

    timestamp: datetime
    symbol: str
    side: str
    price: float
    qty: float
    notional: float
    fee: float
    is_maker: bool
    realized_pnl: float | None = None
    exit_reason: str | None = None


@dataclass
class PortfolioSnapshot:
    """Portfolio value at period boundaries."""

    starting_value: float
    ending_value: float
    high_watermark: float
    low_watermark: float
    cash_balance: float
    positions_value: float


@dataclass
class ComparisonData:
    """v1 vs v2 signal and fill comparison."""

    v1_signal_count: int = 0
    v2_signal_count: int = 0
    agreement_count: int = 0
    v1_only_count: int = 0
    v2_only_count: int = 0
    agreement_rate: float = 0.0
    v1_symbols: set[str] = field(default_factory=set)
    v2_symbols: set[str] = field(default_factory=set)
    symbols_only_v1: set[str] = field(default_factory=set)
    symbols_only_v2: set[str] = field(default_factory=set)
    divergences: list[dict] = field(default_factory=list)


@dataclass
class ReportData:
    """Complete report payload passed to renderers."""

    report_date: date
    period_hours: int
    trade_stats: TradeStats
    pnl: PnLSummary
    positions: list[PositionSnapshot]
    signals: SignalStats
    risk: RiskStats
    period_start: datetime | None = None
    period_end: datetime | None = None
    exit_manager: ExitManagerStats | None = None
    trade_log: list[TradeLogEntry] | None = None
    portfolio: PortfolioSnapshot | None = None
    comparison: ComparisonData | None = None
    exchange_name: str = ""
    generated_at: datetime | None = None
