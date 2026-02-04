"""
Multi-ROC Strategy Configurations
==================================

Defines all test configurations for ROC_MOMO_20M and ROC_MOMO_24H strategies.

Date: January 27, 2026
Purpose: Systematic backtest of Multi-ROC momentum strategies with different exit configurations
"""

from decimal import Decimal
from dataclasses import dataclass
from typing import Optional


@dataclass
class MultiROCConfig:
    """
    Configuration for Multi-ROC strategies (ROC_MOMO_20M and ROC_MOMO_24H)

    Defines both entry and exit parameters for momentum trading strategies.
    """

    # Configuration Metadata
    name: str  # Config name (e.g., "BASELINE", "PURE_MOMENTUM")
    description: str  # Human-readable description

    # ================================================================
    # ENTRY PARAMETERS (shared by both 20M and 24H)
    # ================================================================

    # ROC_MOMO_20M Entry
    roc_20m_buy_threshold: Decimal = Decimal("2.0")  # +2% in 20 minutes
    roc_20m_rsi_low: Decimal = Decimal("45.0")       # RSI must be >= 45
    roc_20m_rsi_high: Decimal = Decimal("60.0")      # RSI must be <= 60

    # ROC_MOMO_24H Entry
    roc_24h_buy_threshold: Decimal = Decimal("10.0")  # +10% in 24 hours
    roc_24h_rsi_low: Decimal = Decimal("45.0")        # RSI must be >= 45
    roc_24h_rsi_high: Decimal = Decimal("55.0")       # RSI must be <= 55

    # Shared
    rsi_window: int = 14  # 14-period RSI (Wilder standard)
    roc_window: int = 20  # 20-period for calculated ROC

    # ================================================================
    # EXIT PARAMETERS - ROC THRESHOLDS
    # ================================================================

    # ROC threshold exits (momentum reversal detection)
    use_roc_threshold_exit: bool = True  # Use ROC reversal as exit signal
    roc_20m_sell_threshold: Decimal = Decimal("-2.0")  # Exit 20M when ROC < -2%
    roc_24h_sell_threshold: Decimal = Decimal("-5.0")  # Exit 24H when 24h% < -5%

    # ================================================================
    # EXIT PARAMETERS - TAKE PROFIT / STOP LOSS
    # ================================================================

    # Take Profit (optional caps)
    take_profit_20m: Optional[Decimal] = None  # None = no cap (pure momentum)
    take_profit_24h: Optional[Decimal] = None  # None = no cap (pure momentum)

    # Stop Loss (emergency exits)
    stop_loss: Optional[Decimal] = None  # None = no hard stop (trust ROC)

    # ================================================================
    # EXIT PARAMETERS - PEAK TRACKING (24H only)
    # ================================================================

    # Peak tracking for 24H runners (let big moves run, exit on reversal)
    use_peak_tracking_24h: bool = True
    peak_tracking_activation_pct: Decimal = Decimal("0.03")  # Activate at +3% profit
    peak_tracking_drawdown_pct: Decimal = Decimal("0.08")    # Exit at 8% drop from peak
    peak_smoothing_periods: int = 1  # SMA smoothing (1 = 5-min candles)

    # ================================================================
    # EXIT PARAMETERS - TRAILING STOP (20M only)
    # ================================================================

    # ATR-based trailing stop for 20M scalps (tight stops for fast moves)
    use_trailing_stop_20m: bool = False
    trailing_stop_activation_pct: Decimal = Decimal("0.03")  # Activate at +3%
    trailing_stop_atr_mult: Decimal = Decimal("2.5")         # Trail at 2.5× ATR
    trailing_step_atr_mult: Decimal = Decimal("0.75")        # Adjust every 0.75× ATR
    trailing_min_distance_pct: Decimal = Decimal("0.02")     # Min: 2%
    trailing_max_distance_pct: Decimal = Decimal("0.04")     # Max: 4%
    atr_window: int = 14  # 14-period ATR
    atr_timeframe: str = "2h"  # 2-hour candles for ATR calculation

    # ================================================================
    # EXIT PARAMETERS - TIME-BASED
    # ================================================================

    # Max hold time (safety net)
    max_hold_hours_20m: int = 4    # 20M scalps: 4 hours max
    max_hold_hours_24h: int = 48   # 24H runners: 48 hours max

    # ================================================================
    # POSITION SIZING
    # ================================================================

    order_size_20m: Decimal = Decimal("10.00")  # $10 per 20M trade
    order_size_24h: Decimal = Decimal("20.00")  # $20 per 24H trade

    # ================================================================
    # FEES
    # ================================================================

    fee_rate: Decimal = Decimal("0.012")  # 1.2% taker fee


# ============================================================================
# CONFIGURATION 1: BASELINE (Current Production)
# ============================================================================

CONFIG_1_BASELINE = MultiROCConfig(
    name="BASELINE",
    description="Current production settings - TP caps at 4% (contradicts momentum!)",

    # Entry: Standard
    roc_20m_buy_threshold=Decimal("2.0"),
    roc_24h_buy_threshold=Decimal("10.0"),

    # Exit: HARD TP CAPS (the problem!)
    use_roc_threshold_exit=True,
    roc_20m_sell_threshold=Decimal("-2.0"),
    roc_24h_sell_threshold=Decimal("-5.0"),

    take_profit_20m=Decimal("0.04"),  # 4% cap (exits too early!)
    take_profit_24h=Decimal("0.04"),  # 4% cap (misses big moves!)
    stop_loss=Decimal("-0.02"),       # -2% stop

    # Peak tracking (never reached because TP=4% exits before activation at 4.5%!)
    use_peak_tracking_24h=True,
    peak_tracking_activation_pct=Decimal("0.045"),  # 4.5%
    peak_tracking_drawdown_pct=Decimal("0.08"),     # 8%

    # Trailing stop disabled
    use_trailing_stop_20m=False,

    # Sizing
    order_size_20m=Decimal("10.00"),
    order_size_24h=Decimal("20.00"),
)


# ============================================================================
# CONFIGURATION 2: PURE MOMENTUM (Aggressive - Let Winners Run!)
# ============================================================================

CONFIG_2_PURE_MOMENTUM = MultiROCConfig(
    name="PURE_MOMENTUM",
    description="Pure momentum - NO TP caps, let ROC and peak tracking handle exits",

    # Entry: Standard
    roc_20m_buy_threshold=Decimal("2.0"),
    roc_24h_buy_threshold=Decimal("10.0"),

    # Exit: ROC thresholds + peak tracking ONLY
    use_roc_threshold_exit=True,
    roc_20m_sell_threshold=Decimal("-2.0"),
    roc_24h_sell_threshold=Decimal("-5.0"),

    # NO TP/SL caps - pure momentum!
    take_profit_20m=None,  # No cap
    take_profit_24h=None,  # No cap
    stop_loss=None,        # No hard stop (trust ROC reversal)

    # Peak tracking (activates earlier, lets big moves run)
    use_peak_tracking_24h=True,
    peak_tracking_activation_pct=Decimal("0.03"),  # 3% (easier to activate)
    peak_tracking_drawdown_pct=Decimal("0.08"),    # 8% drop to exit

    # Trailing stop for 20M (tight stops for scalps)
    use_trailing_stop_20m=True,
    trailing_stop_activation_pct=Decimal("0.03"),
    trailing_stop_atr_mult=Decimal("2.5"),

    # Sizing
    order_size_20m=Decimal("10.00"),
    order_size_24h=Decimal("20.00"),
)


# ============================================================================
# CONFIGURATION 3: WIDE SAFETY NET (Conservative)
# ============================================================================

CONFIG_3_WIDE_SAFETY_NET = MultiROCConfig(
    name="WIDE_SAFETY_NET",
    description="Wide TP caps as safety nets only (20% for 24H, 10% for 20M)",

    # Entry: Standard
    roc_20m_buy_threshold=Decimal("2.0"),
    roc_24h_buy_threshold=Decimal("10.0"),

    # Exit: ROC thresholds + wide safety nets
    use_roc_threshold_exit=True,
    roc_20m_sell_threshold=Decimal("-2.0"),
    roc_24h_sell_threshold=Decimal("-5.0"),

    # Wide TP caps (safety nets for extreme pumps)
    take_profit_20m=Decimal("0.10"),  # 10% cap for scalps
    take_profit_24h=Decimal("0.20"),  # 20% cap for runners
    stop_loss=Decimal("-0.03"),       # -3% stop

    # Peak tracking
    use_peak_tracking_24h=True,
    peak_tracking_activation_pct=Decimal("0.05"),  # 5%
    peak_tracking_drawdown_pct=Decimal("0.08"),    # 8%

    # Trailing stop
    use_trailing_stop_20m=True,
    trailing_stop_activation_pct=Decimal("0.03"),
    trailing_stop_atr_mult=Decimal("2.5"),

    # Sizing
    order_size_20m=Decimal("10.00"),
    order_size_24h=Decimal("20.00"),
)


# ============================================================================
# CONFIGURATION 4: HYBRID (Balanced - Different exits per strategy)
# ============================================================================

CONFIG_4_HYBRID = MultiROCConfig(
    name="HYBRID",
    description="24H: Pure momentum (no TP). 20M: Cap at 8% (scalps don't run as far)",

    # Entry: Standard
    roc_20m_buy_threshold=Decimal("2.0"),
    roc_24h_buy_threshold=Decimal("10.0"),

    # Exit: ROC thresholds
    use_roc_threshold_exit=True,
    roc_20m_sell_threshold=Decimal("-2.0"),
    roc_24h_sell_threshold=Decimal("-5.0"),

    # Hybrid TP: Cap 20M, let 24H run
    take_profit_20m=Decimal("0.08"),  # 8% cap for scalps
    take_profit_24h=None,             # No cap for runners (pure momentum)
    stop_loss=Decimal("-0.025"),      # -2.5% stop

    # Peak tracking (tighter for 24H)
    use_peak_tracking_24h=True,
    peak_tracking_activation_pct=Decimal("0.04"),  # 4%
    peak_tracking_drawdown_pct=Decimal("0.06"),    # 6% (tighter)

    # Trailing stop (looser for 20M)
    use_trailing_stop_20m=True,
    trailing_stop_activation_pct=Decimal("0.03"),
    trailing_stop_atr_mult=Decimal("2.0"),  # 2.0× (tighter)

    # Sizing
    order_size_20m=Decimal("10.00"),
    order_size_24h=Decimal("20.00"),
)


# ============================================================================
# CONFIG REGISTRY (for easy access in backtest)
# ============================================================================

ALL_CONFIGS = {
    "BASELINE": CONFIG_1_BASELINE,
    "PURE_MOMENTUM": CONFIG_2_PURE_MOMENTUM,
    "WIDE_SAFETY_NET": CONFIG_3_WIDE_SAFETY_NET,
    "HYBRID": CONFIG_4_HYBRID,
}


def get_config(name: str) -> MultiROCConfig:
    """Get configuration by name"""
    if name not in ALL_CONFIGS:
        raise ValueError(f"Unknown config: {name}. Available: {list(ALL_CONFIGS.keys())}")
    return ALL_CONFIGS[name]
