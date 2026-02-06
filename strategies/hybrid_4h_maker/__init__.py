"""
4h Hybrid Maker Strategy Plugin

A fee-aware swing momentum strategy with:
- 4h Donchian breakout / ROC-score setup detection
- 1D EMA200 regime filter
- Breakout-retest entry (maker-friendly)
- Fee-multiple profit targets (2x and 4x fees)
- ATR% stop and trail
- Post-only limit orders throughout

State machine:
  FLAT -> SETUP_ACTIVE -> RETEST_ORDER_WORKING -> CHASE_ORDER_WORKING -> IN_POSITION -> FLAT
"""

from strategies.hybrid_4h_maker.strategy import Hybrid4hMakerStrategy

__all__ = ['Hybrid4hMakerStrategy']
