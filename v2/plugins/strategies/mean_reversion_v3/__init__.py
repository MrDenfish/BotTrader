"""Mean-Reversion v3 strategy plugin.

A redesigned strategy plugin that aligns indicator selection with the
mean-reversion philosophy of the BotTrader system. Unlike composite_scoring,
which evolved a momentum tilt over time, v3 is built ground-up as a pure
"buy the dip" engine with a separate trend regime filter.

Stage 1 (regime gate, binary): Supertrend uptrend + ADX < 25 (range-bound)
Stage 2 (composite scoring, mean-reversion-weighted indicators):
    BB Touch, RSI<25, W-Bottom, Volume Divergence, MACD (low weight),
    ROC (low weight). Swing and BB Ratio are deliberately excluded.

The plugin emits BUY signals only. Profits are taken via fixed take-profit
in the exit_manager; losses are cut by hard stop or move-to-breakeven.
"""
