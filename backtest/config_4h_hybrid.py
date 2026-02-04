"""
Configuration for 4h Hybrid Maker Strategy

This module defines all parameters for the fee-aware 4h swing momentum strategy
designed to survive high Coinbase fees through post-only maker orders.

Design intent:
- Trade less often (4h timeframe)
- Avoid exhaustion (pullback-reclaim or breakout-retest entry)
- Fill maker-first (post-only limits)
- Only trade when volatility is large enough to clear fees
"""

from dataclasses import dataclass
from typing import Literal


@dataclass
class Hybrid4hConfig:
    """Configuration for 4h Hybrid Maker Strategy

    All parameters documented per strategy spec:
    /docs/planning/strategy_hybrid_4h_fee_aware_post_only.md
    """

    # ========== Execution Parameters ==========
    name: str = "4H_HYBRID_MAKER"

    # Position sizing
    notional_usd: float = 100.0  # Fixed position size in USD

    # ========== Fee Parameters ==========
    maker_fee: float = 0.006  # Worst-case maker fee (0.6%)
    taker_fee: float = 0.012  # Worst-case taker fee (1.2%)

    # Run with closer-to-actual fees for comparison:
    # maker_fee: float = 0.004  # Closer-to-actual (0.4%)
    # taker_fee: float = 0.008  # Closer-to-actual (0.8%)

    # ========== Indicator Parameters ==========

    # 4h ATR (volatility scale)
    atr_len_4h: int = 14

    # 1D regime filters
    ema200_len_1d: int = 200
    ema50_len_1d: int = 50
    ema_slope_lookback_days: int = 5  # Lookback for slope calculations

    # Phase 2.3.2: Regime filter modes
    use_ema200_slope_filter: bool = False  # Require EMA200 uptrend (slope > 0)
    use_ema50_slope_filter: bool = False  # Require EMA50 uptrend (slope > 0)
    regime_band_pct: float = 0.02  # Softening band (2%) for close near EMA200

    # ========== Setup Parameters (Donchian only for Phase 1) ==========

    setup_mode: Literal["donchian", "roc_score"] = "donchian"

    # Donchian breakout
    donch_len: int = 20  # Look back 20 4h candles (~3.3 days)

    # ROC-score (for Phase 2)
    roc_len_4h: int = 6
    roc_ema_len: int = 3
    roc_score_thresh: float = 1.2

    # Setup TTL
    setup_ttl_bars_4h: int = 6  # Setup expires after 24 hours (Phase 2.2: increased from 3)

    # ========== Phase 2: Bollinger Compression Filter ==========

    use_compression_filter: bool = True
    bb_len: int = 20  # Bollinger band period
    bb_k: float = 2.0  # Bollinger band standard deviation multiplier
    bb_width_window: int = 180  # Rolling window for percentile (30 days @ 4h)
    bb_width_pct_threshold: int = 20  # Only trade when bandwidth <= this percentile

    # ========== Fee-Aware Viability Filter ==========

    vol_min_mult: float = 2.0  # Only trade when atr_pct_4h >= 2× round-trip fees

    # Phase 2.3.2: Conditional viability (lower threshold in compression context)
    use_conditional_viability: bool = False  # Enable compression-aware viability
    vol_min_mult_compression: float = 0.5  # Reduced multiplier when compression_recent=True

    # ========== Entry Parameters ==========

    entry_mode: Literal["breakout_retest", "pullback_reclaim"] = "breakout_retest"

    # Breakout-retest parameters (Phase 1 - simpler)
    retest_band: float = 0.002  # 0.20% band around breakout level
    entry_offset: float = 0.001  # 0.10% below level for post-only buy
    entry_ttl_bars_1m: int = 180  # Cancel entry order after 180 minutes (3 hours)

    # ========== Phase 2: Chase Entry Parameters ==========

    retest_ttl_minutes: int = 180  # Wait for retest before escalating to chase
    enable_chase_entry: bool = True  # Enable maker-first chase fallback
    chase_offset: float = 0.0010  # Chase bid offset (0.10% below current price)
    chase_ttl_minutes: int = 90  # Chase order timeout

    # ========== Phase 2.1: Chase Hardening ==========

    # Max-extension guard: prevent chasing runaway moves
    chase_max_extension: float = 0.005  # 0.5% max price run from breakout level

    # Dynamic chase offset (ATR-based instead of fixed)
    use_dynamic_chase_offset: bool = False  # Disable for baseline, enable for Phase 2.1
    chase_atr_mult: float = 0.5  # Chase offset = 0.5 × ATR% (when dynamic enabled)

    # Compression expansion confirmation
    require_expansion_for_chase: bool = False  # Only chase if BB expanding
    expansion_lookback: int = 2  # Check last 2 4h bars for width increase

    # ========== Phase 2.2: Fill Rate Improvements ==========

    # A1: Recent compression (lookback window)
    compression_lookback_4h: int = 6  # Check last K 4h bars for compression (24h default)

    # Phase 2.3.2: Compression variant analysis
    compression_recent_lookback: int = 12  # Lookback for "recent" compression context (48h default)

    # B1: Immediate retest bid parameters
    retest_offset: float = 0.0015  # 0.15% below breakout level for immediate retest bid

    # B2: Volatility-aware chase offset
    chase_offset_min: float = 0.0005  # Minimum chase offset (0.05%)
    # chase_atr_mult already exists (line 101)

    # B4: Chase repricing
    chase_max_reprices: int = 3  # Maximum number of chase reprices
    chase_reprice_interval_minutes: int = 30  # Time between reprices

    # Repricing safety parameters
    max_step_up_per_reprice: float = 0.0020  # 0.20% max bid increase per reprice
    maker_safety_offset: float = 0.0005  # 0.05% safety offset for post-only preservation

    # Pullback-reclaim parameters (Phase 2)
    ema_pullback_4h: int = 20
    zone_width: float = 0.5  # Zone around EMA = EMA ± (zone_width × ATR%)
    entry_offset2: float = 0.001  # Alternative offset for pullback entry

    # ========== Stop Parameters ==========

    stop_mult: float = 2.0  # Stop at entry_price × (1 - stop_mult × atr_pct_4h)

    # ========== Profit Target Parameters (Fee-Multiple) ==========

    # Fee-based targets (core design)
    tp1_fee_mult: float = 2.0  # TP1 at 2× round-trip maker fees
    tp2_fee_mult: float = 4.0  # TP2 at 4× round-trip maker fees

    # ATR-based minimums (for highly volatile moves)
    tp1_atr_mult: float = 1.0  # TP1 at minimum 1× ATR%
    tp2_atr_mult: float = 2.0  # TP2 at minimum 2× ATR%

    # Scale-out quantities
    tp1_qty_frac: float = 0.40  # Sell 40% at TP1
    tp2_qty_frac: float = 0.40  # Sell 40% at TP2
    # Remaining 20% is runner

    # ========== Runner Management ==========

    trail_mult: float = 2.0  # Trail stop at highest_close_4h × (1 - trail_mult × atr_pct_4h)
    runner_max_bars_4h: int = 10  # Optional time stop for runner (40 hours)
    use_runner_time_stop: bool = False  # Disable for Phase 1

    # ========== Phase 2.4: Stage C - Compression-Based Runner Policy ==========

    # Enable differential runner behavior based on entry compression context
    use_compression_runner_policy: bool = False  # Enable Stage C adaptive runner

    # Runner fraction: compressed entries (tighter ranges expected)
    runner_qty_frac_compressed: float = 0.20  # Default: same as normal (no change)

    # Runner fraction: normal entries (wider ranges expected)
    runner_qty_frac_normal: float = 0.20  # Default: same as compressed (no change)

    # Trail multiplier: compressed entries (tighter trail for breakout compression)
    trail_mult_compressed: float = 2.0  # Default: same as normal trail

    # Trail multiplier: normal entries (wider trail for open-range moves)
    trail_mult_normal: float = 2.0  # Default: same as compressed trail

    # ========== Post-Only Order Simulation ==========

    # Fill rules are hardcoded in engine:
    # - Buy limit fills only if low_1m <= p_buy (conservative)
    # - Sell limit fills only if high_1m >= p_sell (conservative)
    # - Post-only must not cross book at placement
    # - Stops use taker fills (pessimistic)

    # ========== Cooldown ==========

    cooldown_bars_4h: int = 0  # No cooldown for Phase 1

    def __post_init__(self):
        """Validate configuration"""

        # Validate scale-out fractions
        total_frac = self.tp1_qty_frac + self.tp2_qty_frac
        if total_frac >= 1.0:
            raise ValueError(f"TP scale-out fractions must sum to <1.0 (got {total_frac})")

        # Validate fees
        if self.maker_fee >= self.taker_fee:
            raise ValueError("Maker fee should be less than taker fee")

        # Validate setup mode
        if self.setup_mode not in ["donchian", "roc_score"]:
            raise ValueError(f"Invalid setup_mode: {self.setup_mode}")

        # Validate entry mode
        if self.entry_mode not in ["breakout_retest", "pullback_reclaim"]:
            raise ValueError(f"Invalid entry_mode: {self.entry_mode}")

    @property
    def round_trip_maker_fee(self) -> float:
        """Estimated round-trip fee for maker-only execution"""
        return 2.0 * self.maker_fee

    @property
    def runner_qty_frac(self) -> float:
        """Remaining quantity for runner after scale-outs"""
        return 1.0 - self.tp1_qty_frac - self.tp2_qty_frac

    def __repr__(self) -> str:
        """Concise representation for logging"""
        return (
            f"Hybrid4hConfig("
            f"setup={self.setup_mode}, "
            f"entry={self.entry_mode}, "
            f"maker_fee={self.maker_fee:.3%}, "
            f"taker_fee={self.taker_fee:.3%}, "
            f"tp1={self.tp1_fee_mult:.1f}×fees, "
            f"tp2={self.tp2_fee_mult:.1f}×fees)"
        )


# ========== Preset Configurations ==========

def get_default_config() -> Hybrid4hConfig:
    """Default configuration for BTC-USD starting point"""
    return Hybrid4hConfig()


def get_conservative_config() -> Hybrid4hConfig:
    """More conservative: wider stops, higher vol filter"""
    return Hybrid4hConfig(
        stop_mult=2.5,
        vol_min_mult=2.5,
        tp1_fee_mult=2.5,
        tp2_fee_mult=5.0,
    )


def get_aggressive_config() -> Hybrid4hConfig:
    """More aggressive: tighter stops, lower vol filter"""
    return Hybrid4hConfig(
        stop_mult=1.5,
        vol_min_mult=1.5,
        tp1_fee_mult=1.5,
        tp2_fee_mult=3.0,
    )


def get_closer_to_actual_fees_config() -> Hybrid4hConfig:
    """Use closer-to-actual Coinbase fee rates"""
    return Hybrid4hConfig(
        maker_fee=0.004,  # 0.4%
        taker_fee=0.008,  # 0.8%
    )


def get_phase2_baseline_config() -> Hybrid4hConfig:
    """
    Phase 2 baseline: compression filter + chase entry + expanded targets

    Based on Phase 1 optimizer results showing potential at:
    - donch_len=10, vol_min_mult=1.0

    Phase 2 adds:
    - Bollinger bandwidth compression filter
    - Maker-first chase entry fallback
    - Larger profit targets (TP1=3×, TP2=8×)
    - Adjusted scale-out (30%/50%/20%)
    - Wider stops (2.5×)
    """
    return Hybrid4hConfig(
        # Use closer-to-actual fees
        maker_fee=0.004,
        taker_fee=0.008,

        # Focus on productive region from Phase 1
        donch_len=10,
        vol_min_mult=1.0,

        # Phase 2: Compression filter
        use_compression_filter=True,
        bb_width_pct_threshold=20,

        # Phase 2: Chase entry
        retest_ttl_minutes=120,
        enable_chase_entry=True,
        chase_offset=0.0010,
        chase_ttl_minutes=60,

        # Phase 2: Expanded targets
        tp1_fee_mult=3.0,
        tp2_fee_mult=8.0,

        # Phase 2: Adjusted scale-out
        tp1_qty_frac=0.30,
        tp2_qty_frac=0.50,

        # Phase 2: Wider stops
        stop_mult=2.5,
    )


def get_phase2_1_hardened_config() -> Hybrid4hConfig:
    """
    Phase 2.1 hardened: baseline + chase guards + dynamic offset + expansion filter

    Phase 2.1 hardening features:
    - Max-extension guard (0.5% runaway protection)
    - Dynamic ATR-scaled chase offset
    - Compression expansion confirmation
    """
    return Hybrid4hConfig(
        # Use closer-to-actual fees
        maker_fee=0.004,
        taker_fee=0.008,

        # Focus on productive region from Phase 1
        donch_len=10,
        vol_min_mult=1.0,

        # Phase 2: Compression filter
        use_compression_filter=True,
        bb_width_pct_threshold=20,

        # Phase 2: Chase entry
        retest_ttl_minutes=120,
        enable_chase_entry=True,
        chase_offset=0.0010,  # Fallback fixed offset
        chase_ttl_minutes=60,

        # Phase 2.1: Chase hardening
        chase_max_extension=0.005,  # 0.5% max runaway
        use_dynamic_chase_offset=True,  # ATR-based offset
        chase_atr_mult=0.5,  # 0.5× ATR%
        require_expansion_for_chase=True,  # Expanding volatility required
        expansion_lookback=2,  # Check last 2 4h bars

        # Phase 2: Expanded targets
        tp1_fee_mult=3.0,
        tp2_fee_mult=8.0,

        # Phase 2: Adjusted scale-out
        tp1_qty_frac=0.30,
        tp2_qty_frac=0.50,

        # Phase 2: Wider stops
        stop_mult=2.5,
    )


def get_phase2_2_baseline_config() -> Hybrid4hConfig:
    """
    Phase 2.2 baseline: restore fill rate + reach 2-4 trades/month

    Phase 2.2 improvements over 2.1:
    - Recent compression check (last 6 bars = 24h lookback)
    - Looser compression threshold (20 → 30 percentile)
    - Shorter BB window (180 → 120 bars for more adaptive)
    - Immediate retest bid placement (don't wait for conditions)
    - Earlier chase escalation (120 → 45 min retest TTL)
    - Volatility-aware chase offset (0.25× ATR%)
    - Chase repricing (up to 3 reprices, 30min intervals)
    - Longer setup TTL (12h → 24h)

    Target: 2-4 trades/month with improved fill rate
    """
    return Hybrid4hConfig(
        # Use closer-to-actual fees
        maker_fee=0.004,
        taker_fee=0.008,

        # Focus on productive region
        donch_len=10,
        vol_min_mult=1.0,

        # Phase 2.2: Looser compression filter
        use_compression_filter=True,
        bb_width_window=120,  # More adaptive (was 180)
        bb_width_pct_threshold=30,  # Looser (was 20)
        compression_lookback_4h=6,  # 24h recent compression check

        # Phase 2.2: Extended setup TTL
        setup_ttl_bars_4h=6,  # 24h (was 12h)

        # Phase 2.2: Immediate retest bid placement
        retest_offset=0.0015,  # 0.15% below breakout level
        retest_ttl_minutes=45,  # Earlier escalation (was 120)

        # Phase 2.2: Chase entry with volatility-aware offset and repricing
        enable_chase_entry=True,
        chase_offset_min=0.0005,  # 0.05% minimum
        chase_atr_mult=0.25,  # 0.25× ATR% for chase offset
        use_dynamic_chase_offset=True,  # Enable ATR-based chase
        chase_max_extension=0.050,  # 5.0% max runaway protection (very loose for Phase 2.2 testing)
        chase_ttl_minutes=120,  # 2 hours
        chase_max_reprices=3,  # Allow 3 reprices
        chase_reprice_interval_minutes=30,  # Every 30 minutes

        # Phase 2.2: Repricing safety
        max_step_up_per_reprice=0.0020,  # 0.20% max increase per reprice
        maker_safety_offset=0.0005,  # 0.05% safety for post-only

        # Phase 2: Expanded targets
        tp1_fee_mult=3.0,
        tp2_fee_mult=8.0,

        # Phase 2: Adjusted scale-out
        tp1_qty_frac=0.30,
        tp2_qty_frac=0.50,

        # Phase 2: Wider stops
        stop_mult=2.5,
    )


def get_phase2_3_roc_baseline_config() -> Hybrid4hConfig:
    """
    Phase 2.3 ROC-score baseline: Momentum-based setup mode

    Key differences from Phase 2.2 Donchian:
    - setup_mode: "roc_score" (normalized momentum trigger)
    - Uses ROC / ATR% threshold instead of Donchian breakout
    - Same entry/exit mechanics (retest, chase, TPs, trail)
    - Same compression filter (optional)

    Target: Diversify signal sources, reduce regime dependency
    """
    return Hybrid4hConfig(
        # Phase 2.3: ROC-score setup mode
        setup_mode="roc_score",

        # ROC parameters
        roc_len_4h=6,  # 24h rate of change (6 × 4h)
        roc_ema_len=3,  # 12h smoothing (3 × 4h)
        roc_score_thresh=1.2,  # Normalized momentum threshold

        # Use closer-to-actual fees
        maker_fee=0.004,
        taker_fee=0.008,

        # Volatility filter
        vol_min_mult=1.0,

        # Phase 2.3: Optional compression filter (same as Donchian)
        use_compression_filter=True,
        bb_width_window=120,
        bb_width_pct_threshold=30,
        compression_lookback_4h=6,

        # Setup TTL
        setup_ttl_bars_4h=6,  # 24h

        # Entry parameters (same as Phase 2.2)
        retest_offset=0.0015,
        retest_ttl_minutes=45,

        # Chase entry
        enable_chase_entry=True,
        chase_offset_min=0.0005,
        chase_atr_mult=0.25,
        use_dynamic_chase_offset=True,
        chase_max_extension=0.050,
        chase_ttl_minutes=120,
        chase_max_reprices=3,
        chase_reprice_interval_minutes=30,
        max_step_up_per_reprice=0.0020,
        maker_safety_offset=0.0005,

        # Exits (same as Phase 2.2)
        tp1_fee_mult=2.5,
        tp2_fee_mult=5.0,
        stop_mult=2.5,
        trail_mult=1.5,
    )


def get_phase2_4_stage_c_test_config() -> Hybrid4hConfig:
    """
    Phase 2.4 Stage C test: ROC baseline + differential runner policy

    This config enables Stage C with OBVIOUS parameter differences to verify
    the compression-based runner policy switches correctly:
    - Compressed entries: 30% runner, 1.5x trail (tighter)
    - Normal entries: 10% runner, 2.5x trail (wider)

    Use for validation testing only.
    """
    config = get_phase2_3_roc_baseline_config()

    # Enable Stage C runner policy
    config.use_compression_runner_policy = True

    # Differential parameters (OBVIOUS differences for verification)
    config.runner_qty_frac_compressed = 0.30  # 30% runner for compressed
    config.runner_qty_frac_normal = 0.10      # 10% runner for normal
    config.trail_mult_compressed = 1.5        # Tighter trail for compressed
    config.trail_mult_normal = 2.5            # Wider trail for normal

    return config


if __name__ == "__main__":
    # Test configuration
    cfg = get_default_config()
    print(cfg)
    print(f"\nRound-trip maker fee: {cfg.round_trip_maker_fee:.3%}")
    print(f"Runner qty fraction: {cfg.runner_qty_frac:.1%}")

    print("\n--- Conservative ---")
    print(get_conservative_config())

    print("\n--- Aggressive ---")
    print(get_aggressive_config())

    print("\n--- Closer-to-Actual Fees ---")
    print(get_closer_to_actual_fees_config())

    print("\n--- Phase 2 Baseline ---")
    print(get_phase2_baseline_config())

    print("\n--- Phase 2.3 ROC Baseline ---")
    print(get_phase2_3_roc_baseline_config())

    print("\n--- Phase 2.4 Stage C Test ---")
    print(get_phase2_4_stage_c_test_config())
