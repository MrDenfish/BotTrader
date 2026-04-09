"""Configuration for the composite scoring strategy.

All parameters from sighook/signal_manager.py and sighook/indicators.py
consolidated into a single dataclass. Every field has a production default.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CompositeScoreConfig:
    """All tunable parameters for the composite scoring strategy."""

    # ------------------------------------------------------------------
    # Scoring thresholds
    # ------------------------------------------------------------------
    score_buy_target: float = 5.5
    score_sell_target: float = 5.5

    # ------------------------------------------------------------------
    # Bollinger Bands
    # ------------------------------------------------------------------
    bb_window: int = 20
    bb_std: float = 2.0
    buy_ratio: float = 1.0
    sell_ratio: float = 0.95

    # ------------------------------------------------------------------
    # MACD
    # ------------------------------------------------------------------
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # ------------------------------------------------------------------
    # RSI
    # ------------------------------------------------------------------
    rsi_window: int = 14
    rsi_buy: float = 20.0    # Oversold threshold (signal fires if RSI < this)
    rsi_sell: float = 80.0   # Overbought threshold (signal fires if RSI > this)

    # ------------------------------------------------------------------
    # ROC (Rate of Change)
    # ------------------------------------------------------------------
    roc_window: int = 4
    roc_buy_threshold: float = 5.0
    roc_sell_threshold: float = -2.0

    # ------------------------------------------------------------------
    # Volatility & Swing
    # ------------------------------------------------------------------
    sma_volatility: int = 30
    atr_window: int = 14
    swing_window: int = 20

    # ------------------------------------------------------------------
    # Regime filter
    # ------------------------------------------------------------------
    regime_filter_enabled: bool = False       # Off by default for backward compat
    regime_max_atr_percentile: float = 60.0   # Block buys when ATR percentile > this
    regime_require_uptrend: bool = False       # Optionally require positive SMA slope

    # ------------------------------------------------------------------
    # Volume confirmation gate
    # ------------------------------------------------------------------
    volume_confirm_buy: bool = True        # Require above-avg volume to allow buys
    volume_confirm_threshold: float = 0.7  # Min RVOL (current / rolling avg) for buy

    # ------------------------------------------------------------------
    # Volume divergence
    # ------------------------------------------------------------------
    volume_div_window: int = 10  # Lookback bars for price/volume slope comparison

    # ------------------------------------------------------------------
    # Strategy weights  (indicator name → weight)
    # ------------------------------------------------------------------
    weights: dict[str, float] = field(default_factory=lambda: {
        "Buy Ratio": 1.2,  "Buy Touch": 1.5,  "W-Bottom": 2.0,
        "Buy RSI": 1.5,    "Buy ROC": 2.0,    "Buy MACD": 1.8,
        "Buy Swing": 2.2,  "Buy Volume Div": 1.5,
        "Sell Ratio": 1.2,  "Sell Touch": 1.5,  "M-Top": 2.0,
        "Sell RSI": 1.5,    "Sell ROC": 2.0,    "Sell MACD": 1.8,
        "Sell Swing": 2.2,  "Sell Volume Div": 1.5,
    })

    # ------------------------------------------------------------------
    # Guardrails
    # ------------------------------------------------------------------
    flip_hysteresis_pct: float = 0.10   # Require +10% over target to flip side
    cooldown_bars: int = 7              # Bars to block opposite side after flip
    min_indicators_required: int = 2    # Multi-indicator confirmation gate
    min_sell_indicators_required: int = 0  # Sell-side override (0 = use min_indicators_required)
    high_conviction_threshold: int = 4   # Indicator count >= this uses "score_high" trigger for sizing
    require_trend_for_buy: bool = True     # Require ≥1 trend indicator (MACD/ROC/Swing) for buys

    # Post-loss buy lockout: block re-entry after exit manager sells at a loss.
    # Prevents death-spiral where oversold indicators trigger immediate re-buy.
    loss_lockout_bars: int = 12         # Base lockout bars (~1h at 5-min candles)
    loss_lockout_scale: dict[str, float] = field(default_factory=lambda: {
        "hard_stop": 2.0,      # 2× base = 24 bars = 2 hours
        "soft_stop": 1.0,      # 1× base = 12 bars = 1 hour
        "trailing_stop": 0.5,  # 0.5× base = 6 bars = 30 minutes
    })

    # ------------------------------------------------------------------
    # ROC Momentum strategies (priority over composite scoring)
    # ------------------------------------------------------------------
    enable_roc_20m_momentum: bool = True   # Set False to disable 20m momentum entries
    roc_20m_buy_threshold: float = 2.0
    roc_20m_sell_threshold: float = -2.0
    roc_20m_rsi_buy_range: tuple[float, float] = (45.0, 60.0)
    roc_20m_rsi_sell_range: tuple[float, float] = (40.0, 55.0)

    enable_roc_24h_momentum: bool = True   # Set False to disable 24h momentum entries
    roc_24h_buy_threshold: float = 8.5
    roc_24h_sell_threshold: float = -5.0
    roc_24h_rsi_range: tuple[float, float] = (45.0, 55.0)

    # ------------------------------------------------------------------
    # ADX trend strength gate
    # ------------------------------------------------------------------
    adx_gate_enabled: bool = True       # Require minimum trend strength for buys
    adx_period: int = 14                # Standard ADX lookback period
    adx_min_threshold: float = 20.0     # Textbook threshold: < 20 = no trend

    # ------------------------------------------------------------------
    # Red-day gate
    # ------------------------------------------------------------------
    allow_buys_on_red_day: bool = True  # If False, block buys when 24h change < 0

    # ------------------------------------------------------------------
    # Candle aggregation
    # ------------------------------------------------------------------
    candle_interval_minutes: int = 1  # 1=raw 1-min candles, 5=aggregate to 5-min

    # ------------------------------------------------------------------
    # Buffer / warmup
    # ------------------------------------------------------------------
    buffer_size: int = 500   # Rolling candle buffer for indicator calculation
    min_bars: int = 80       # Minimum bars before evaluating (~3x MACD slow period)
