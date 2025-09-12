# botreport/models.py
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class PositionRow:
    symbol: str
    side: str              # "long" or "short"
    qty: float
    avg_price: float
    notional: float
    pct_total: float

@dataclass
class ExposureBlock:
    total_notional: float = 0.0
    invested_pct_of_equity: Optional[float] = None
    leverage_used: Optional[float] = None
    long_notional: Optional[float] = None
    short_notional: Optional[float] = None
    net_exposure_abs: Optional[float] = None
    net_exposure_pct: Optional[float] = None
    positions: Optional[List[PositionRow]] = None

@dataclass
class MetricsBlock:
    as_of_iso: str
    window_label: str
    source_label: str
    # headline pnl
    realized_pnl: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    # trade stats
    total_trades: Optional[int] = None
    breakeven_trades: Optional[int] = None
    win_rate_pct: Optional[float] = None
    avg_win: Optional[float] = None
    avg_loss: Optional[float] = None
    avg_w_over_avg_l: Optional[float] = None
    profit_factor: Optional[float] = None
    expectancy_per_trade: Optional[float] = None
    mean_pnl_per_trade: Optional[float] = None
    stdev_pnl_per_trade: Optional[float] = None
    sharpe_like_per_trade: Optional[float] = None
    # drawdown (window)
    max_drawdown_pct: Optional[float] = None
    max_drawdown_abs: Optional[float] = None

@dataclass
class ReportBundle:
    """Hand-off struct from data_access/compute to renderer."""
    metrics: MetricsBlock
    exposure: ExposureBlock
    notes: str
    csv_note: Optional[str] = None

