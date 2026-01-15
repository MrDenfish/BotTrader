"""
Backtest Configuration

Defines strategy parameters and backtest settings.
"""

from decimal import Decimal
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class StrategyConfig:
    """Trading strategy parameters - matches production .env"""

    # ROC Momentum Parameters (5-minute ROC, not 24-hour!)
    roc_buy_threshold: Decimal = Decimal("7.5")  # 7.5% positive 5-min ROC (from .env)
    roc_sell_threshold: Decimal = Decimal("-5.0")  # -5.0% negative 5-min ROC (from .env)
    roc_window: int = 5  # 5-period ROC calculation (ROC_WINDOW from constants)

    # ROC Acceleration Gate (production requirement)
    roc_accel_enabled: bool = True  # Check ROC acceleration
    roc_accel_min: Decimal = Decimal("0.3")  # Minimum |ROC_Diff| threshold
    roc_accel_std_mult: Decimal = Decimal("0.5")  # Multiplier for ROC_Diff_STD20

    # RSI Filter (production requirement - only trade in neutral zone)
    rsi_filter_enabled: bool = True  # Require RSI in neutral zone
    rsi_neutral_low: Decimal = Decimal("40.0")  # RSI must be >= 40
    rsi_neutral_high: Decimal = Decimal("60.0")  # RSI must be <= 60
    rsi_window: int = 7  # RSI calculation window (from constants)

    # Exit Strategy Parameters (matches production .env exactly)
    take_profit_pct: Decimal = Decimal("0.025")  # 2.5% take profit (from .env TAKE_PROFIT)
    stop_loss_pct: Decimal = Decimal("0.015")  # 1.5% stop loss (from .env STOP_LOSS)

    # Peak Tracking Exit (PRICE-based, not ROC-based!)
    peak_tracking_enabled: bool = True  # Enabled in production .env
    peak_drawdown_pct: Decimal = Decimal("0.05")  # 5% price drop from peak
    peak_min_profit_pct: Decimal = Decimal("0.06")  # Must reach +6% to activate
    peak_breakeven_pct: Decimal = Decimal("0.06")  # Breakeven stop at +6%
    peak_smoothing_periods: int = 1  # 5-min smoothing (1 candle = 5min)
    peak_max_hold_hours: int = 24  # 24-hour max hold time

    # Order Sizing
    order_size_roc: Decimal = Decimal("25.00")  # $25 for ROC momentum trades
    order_size_signal: Decimal = Decimal("15.00")  # $15 for standard signals

    # Indicator Thresholds (for standard scoring - not used in ROC momentum)
    rsi_buy: float = 35.0
    rsi_sell: float = 65.0
    score_buy_target: Decimal = Decimal("2.0")
    score_sell_target: Decimal = Decimal("2.0")

    # Risk Management
    max_position_size_usd: Decimal = Decimal("1000.00")
    min_order_size_usd: Decimal = Decimal("1.00")
    fee_rate: Decimal = Decimal("0.012")  # 1.2% taker fee (worst case, from .env TAKER_FEE)

    # Time-based Exits
    max_hold_time_hours: Optional[int] = None  # Handled by peak tracking


@dataclass
class BacktestConfig:
    """Backtest execution parameters"""

    # Date Range
    start_date: datetime
    end_date: datetime

    # Initial Conditions
    initial_capital: Decimal = Decimal("10000.00")  # $10k starting capital

    # Execution Settings
    slippage_pct: Decimal = Decimal("0.001")  # 0.1% slippage

    # Symbols to Test
    symbols: list[str] = None  # None = all available symbols

    # Output Settings
    verbose: bool = True
    save_trades: bool = True

    def __post_init__(self):
        if self.symbols is None:
            self.symbols = []  # Will be populated from data


# Preset Configurations
CURRENT_PRODUCTION = StrategyConfig(
    # Matches production .env exactly
    roc_buy_threshold=Decimal("7.5"),
    roc_sell_threshold=Decimal("-5.0"),
    take_profit_pct=Decimal("0.025"),  # 2.5% (from .env)
    stop_loss_pct=Decimal("0.015"),    # 1.5% (from .env)
    peak_tracking_enabled=True,
    peak_drawdown_pct=Decimal("0.05"),
    peak_min_profit_pct=Decimal("0.06"),
    order_size_roc=Decimal("25.00"),
    order_size_signal=Decimal("15.00"),
)

# Test variations for parameter optimization

# === OPTIMIZATION TEST SUITE (Jan 2026) ===

# Test 1: Conservative Improvement (Tighter Filters + Slightly Wider Exits)
TEST_1_CONSERVATIVE = StrategyConfig(
    # Entry: Slightly stricter
    roc_buy_threshold=Decimal("8.5"),      # Raised from 7.5% (fewer entries)
    roc_sell_threshold=Decimal("-5.0"),

    # RSI Filter: Tighter neutral zone
    rsi_filter_enabled=True,
    rsi_neutral_low=Decimal("45.0"),       # Was 40.0 (tighter)
    rsi_neutral_high=Decimal("55.0"),      # Was 60.0 (tighter)
    rsi_window=7,  # Keep same for now

    # Exits: Slightly wider to let momentum develop
    take_profit_pct=Decimal("0.035"),      # 3.5% (was 2.5%)
    stop_loss_pct=Decimal("0.018"),        # 1.8% (was 1.5%)

    # Peak tracking: Same as production
    peak_tracking_enabled=True,
    peak_drawdown_pct=Decimal("0.05"),
    peak_min_profit_pct=Decimal("0.06"),
    peak_breakeven_pct=Decimal("0.06"),

    order_size_roc=Decimal("25.00"),
    order_size_signal=Decimal("15.00"),
)

# Test 2: Moderate (Longer RSI + Wider Exits)
TEST_2_MODERATE = StrategyConfig(
    # Entry: Same as Test 1
    roc_buy_threshold=Decimal("8.5"),
    roc_sell_threshold=Decimal("-5.0"),

    # RSI Filter: Tighter + industry-standard window
    rsi_filter_enabled=True,
    rsi_neutral_low=Decimal("45.0"),
    rsi_neutral_high=Decimal("55.0"),
    rsi_window=14,  # ← Changed from 7 (Wilder's standard, reduces noise)

    # Exits: Wider to capture momentum runs
    take_profit_pct=Decimal("0.040"),      # 4.0% (was 2.5%)
    stop_loss_pct=Decimal("0.020"),        # 2.0% (was 1.5%)

    # Peak tracking: Lower activation
    peak_tracking_enabled=True,
    peak_drawdown_pct=Decimal("0.05"),
    peak_min_profit_pct=Decimal("0.045"),  # 4.5% (was 6%)
    peak_breakeven_pct=Decimal("0.045"),

    order_size_roc=Decimal("25.00"),
    order_size_signal=Decimal("15.00"),
)

# Test 3: Aggressive (Lower ROC Threshold + Tight Exits + Fast Churn)
TEST_3_AGGRESSIVE = StrategyConfig(
    # Entry: LOWER threshold to catch more moves
    roc_buy_threshold=Decimal("6.0"),      # Lowered from 7.5% (more entries)
    roc_sell_threshold=Decimal("-5.0"),

    # RSI Filter: Tighter
    rsi_filter_enabled=True,
    rsi_neutral_low=Decimal("45.0"),
    rsi_neutral_high=Decimal("55.0"),
    rsi_window=14,  # Industry standard

    # Exits: Tight exits + aggressive peak tracking
    take_profit_pct=Decimal("0.030"),      # 3.0% (between current and wide)
    stop_loss_pct=Decimal("0.015"),        # 1.5% (keep tight)

    # Peak tracking: Early activation
    peak_tracking_enabled=True,
    peak_drawdown_pct=Decimal("0.04"),     # 4% (tighter - was 5%)
    peak_min_profit_pct=Decimal("0.025"),  # 2.5% (much lower - was 6%)
    peak_breakeven_pct=Decimal("0.025"),

    order_size_roc=Decimal("25.00"),
    order_size_signal=Decimal("15.00"),
)

# Legacy Option A: Wider TP to let winners run
OPTION_A_WIDER_TP = StrategyConfig(
    roc_buy_threshold=Decimal("7.5"),
    roc_sell_threshold=Decimal("-5.0"),
    take_profit_pct=Decimal("0.045"),  # 4.5% TP (was 2.5%)
    stop_loss_pct=Decimal("0.015"),    # 1.5% SL (keep same)
    peak_tracking_enabled=True,
    peak_drawdown_pct=Decimal("0.05"),
    peak_min_profit_pct=Decimal("0.04"),  # Lower activation (4% vs 6%)
    order_size_roc=Decimal("25.00"),
    order_size_signal=Decimal("15.00"),
)

# Option 2: Wider SL to give momentum room
OPTION_2_WIDER_SL = StrategyConfig(
    roc_buy_threshold=Decimal("7.5"),
    roc_sell_threshold=Decimal("-5.0"),
    take_profit_pct=Decimal("0.045"),  # 4.5% TP
    stop_loss_pct=Decimal("0.030"),    # 3.0% SL (was 1.5%)
    peak_tracking_enabled=True,
    peak_drawdown_pct=Decimal("0.05"),
    peak_min_profit_pct=Decimal("0.04"),  # 4% activation
    order_size_roc=Decimal("25.00"),
    order_size_signal=Decimal("15.00"),
)

# Option 3: Much wider TP with aggressive peak tracking
OPTION_3_PEAK_FOCUS = StrategyConfig(
    roc_buy_threshold=Decimal("7.5"),
    roc_sell_threshold=Decimal("-5.0"),
    take_profit_pct=Decimal("0.060"),  # 6.0% TP
    stop_loss_pct=Decimal("0.020"),    # 2.0% SL
    peak_tracking_enabled=True,
    peak_drawdown_pct=Decimal("0.05"),     # 5% drop from peak
    peak_min_profit_pct=Decimal("0.030"),  # Activate at 3% profit!
    peak_breakeven_pct=Decimal("0.030"),   # Breakeven at 3%
    order_size_roc=Decimal("25.00"),
    order_size_signal=Decimal("15.00"),
)

# Legacy: Symmetric TP/SL
AGGRESSIVE_TP_SL = StrategyConfig(
    roc_buy_threshold=Decimal("7.5"),
    roc_sell_threshold=Decimal("-5.0"),
    take_profit_pct=Decimal("0.040"),  # 4.0% TP
    stop_loss_pct=Decimal("0.040"),    # 4.0% SL
    peak_tracking_enabled=True,
    peak_drawdown_pct=Decimal("0.05"),
    order_size_roc=Decimal("25.00"),
    order_size_signal=Decimal("15.00"),
)

CONSERVATIVE_TP_SL = StrategyConfig(
    roc_buy_threshold=Decimal("7.5"),
    roc_sell_threshold=Decimal("-5.0"),
    take_profit_pct=Decimal("0.030"),  # 3.0% TP
    stop_loss_pct=Decimal("0.050"),    # 5.0% SL
    peak_tracking_enabled=True,
    peak_drawdown_pct=Decimal("0.05"),
    order_size_roc=Decimal("25.00"),
    order_size_signal=Decimal("15.00"),
)

ROC_SENSITIVE = StrategyConfig(
    roc_buy_threshold=Decimal("5.0"),   # Lower threshold (more trades)
    roc_sell_threshold=Decimal("-3.0"),
    take_profit_pct=Decimal("0.035"),
    stop_loss_pct=Decimal("0.045"),
    peak_tracking_enabled=True,
    peak_drawdown_pct=Decimal("0.04"),  # 4% drop (tighter exit)
    order_size_roc=Decimal("25.00"),
    order_size_signal=Decimal("15.00"),
)

ROC_STRICT = StrategyConfig(
    roc_buy_threshold=Decimal("10.0"),   # Higher threshold (fewer trades)
    roc_sell_threshold=Decimal("-7.0"),
    take_profit_pct=Decimal("0.035"),
    stop_loss_pct=Decimal("0.045"),
    peak_tracking_enabled=True,
    peak_drawdown_pct=Decimal("0.07"),  # 7% drop (looser exit)
    order_size_roc=Decimal("25.00"),
    order_size_signal=Decimal("15.00"),
)
