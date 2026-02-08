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
    # Strategy weights  (indicator name → weight)
    # ------------------------------------------------------------------
    weights: dict[str, float] = field(default_factory=lambda: {
        "Buy Ratio": 1.2,  "Buy Touch": 1.5,  "W-Bottom": 2.0,
        "Buy RSI": 1.5,    "Buy ROC": 2.0,    "Buy MACD": 1.8,
        "Buy Swing": 2.2,
        "Sell Ratio": 1.2,  "Sell Touch": 1.5,  "M-Top": 2.0,
        "Sell RSI": 1.5,    "Sell ROC": 2.0,    "Sell MACD": 1.8,
        "Sell Swing": 2.2,
    })

    # ------------------------------------------------------------------
    # Guardrails
    # ------------------------------------------------------------------
    flip_hysteresis_pct: float = 0.10   # Require +10% over target to flip side
    cooldown_bars: int = 7              # Bars to block opposite side after flip
    min_indicators_required: int = 2    # Multi-indicator confirmation gate

    # ------------------------------------------------------------------
    # ROC Momentum strategies (priority over composite scoring)
    # ------------------------------------------------------------------
    roc_20m_buy_threshold: float = 2.0
    roc_20m_sell_threshold: float = -2.0
    roc_20m_rsi_buy_range: tuple[float, float] = (45.0, 60.0)
    roc_20m_rsi_sell_range: tuple[float, float] = (40.0, 55.0)

    roc_24h_buy_threshold: float = 8.5
    roc_24h_sell_threshold: float = -5.0
    roc_24h_rsi_range: tuple[float, float] = (45.0, 55.0)

    # ------------------------------------------------------------------
    # Red-day gate
    # ------------------------------------------------------------------
    allow_buys_on_red_day: bool = True  # If False, block buys when 24h change < 0

    # ------------------------------------------------------------------
    # Buffer / warmup
    # ------------------------------------------------------------------
    buffer_size: int = 500   # Rolling candle buffer for indicator calculation
    min_bars: int = 40       # Minimum bars before evaluating
