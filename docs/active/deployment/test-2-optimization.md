# TEST 2 OPTIMIZATION SETTINGS
# Backtest Results: -$1.94 (vs -$27.00 production) | 57.9% win rate | 19 trades
# Apply these changes to .env on both local and AWS

# ROC Momentum Entry (stricter)
ROC_5MIN_BUY_THRESHOLD=8.5      # Was 7.5
ROC_5MIN_SELL_THRESHOLD=5.0     # Unchanged

# RSI Filter (industry standard + tighter neutral zone)
RSI_WINDOW=14                   # Was 7
# Note: RSI neutral zone changed in signal_manager.py from 40-60 to 45-55

# Exit Levels (wider to capture momentum)
TAKE_PROFIT=0.040               # Was 0.025 (4.0% vs 2.5%)
STOP_LOSS=-0.020                # Was -0.015 (2.0% vs 1.5%)

# Peak Tracking (lower activation threshold)
PEAK_TRACKING_ENABLED=true
PEAK_TRACKING_DRAWDOWN_PCT=0.05
PEAK_TRACKING_MIN_PROFIT_PCT=0.045    # Was 0.06 (4.5% vs 6.0%)
PEAK_TRACKING_BREAKEVEN_PCT=0.045     # Was 0.06 (4.5% vs 6.0%)
PEAK_TRACKING_SMOOTHING_MINS=5
PEAK_TRACKING_MAX_HOLD_MINS=1440
PEAK_TRACKING_TRIGGERS=ROC_MOMO,ROC_MOMO_OVERRIDE,ROC
