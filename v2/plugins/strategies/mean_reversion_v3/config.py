"""Configuration for mean_reversion_v3 strategy."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MeanReversionV3Config:
    # Aggregation / buffer
    candle_interval_minutes: int = 5
    buffer_size: int = 200
    min_bars: int = 80

    # Bollinger Bands
    bb_window: int = 10
    bb_std: float = 2.0

    # MACD
    macd_fast: int = 8
    macd_slow: int = 21
    macd_signal: int = 5

    # RSI
    rsi_window: int = 14
    rsi_buy: float = 25.0

    # ROC
    roc_window: int = 4
    roc_buy_threshold: float = 2.0

    # ATR / volatility (used for true-range and volume div windows)
    atr_window: int = 14
    volume_div_window: int = 10

    # Supertrend (Stage 1 regime filter)
    supertrend_period: int = 10
    supertrend_multiplier: float = 3.0

    # ADX (Stage 1 regime filter — note INVERTED direction from composite_scoring)
    adx_period: int = 14
    adx_max_threshold: float = 25.0  # Only buy when ADX < this (range-bound regime)

    # Volume confirmation gate
    volume_confirm_buy: bool = True
    volume_confirm_threshold: float = 0.7

    # Composite scoring
    score_buy_target: float = 3.0
    min_indicators_required: int = 3
    high_conviction_threshold: int = 4

    # Indicator weights — mean-reversion focused
    # Dropped: Buy Swing (not a trend or reversal indicator on 5-min candles)
    # Dropped: Buy Ratio (BB compression is directional-neutral)
    weights: dict[str, float] = field(default_factory=lambda: {
        "Buy Touch": 2.0,        # pure mean-rev: lower-band touch
        "Buy RSI": 2.0,          # pure mean-rev: oversold
        "W-Bottom": 2.0,         # reversal pattern
        "Buy Volume Div": 1.8,   # selling exhaustion
        "Buy MACD": 1.2,         # bullish-cross timing helper
        "Buy ROC": 0.8,          # bounce-confirmation, not driver
    })

    # Guardrails
    cooldown_bars: int = 2
    loss_lockout_bars: int = 12
    loss_lockout_scale: dict[str, float] = field(default_factory=lambda: {
        "hard_stop": 2.0,
        "breakeven_stop": 1.0,
        "take_profit": 0.5,
        "stale_exit": 1.0,
    })
