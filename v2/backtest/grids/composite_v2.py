"""Grid search parameter definitions for composite scoring strategy v2.

Updated for current config:
- ATR-based hard stops (hard_stop_mode: "atr")
- roc_momo_24h disabled
- Trend filter enabled
- Exit manager at risk index 2
- 1-min ATR always clamps to floor, so hard_stop_min_pct is the key param

Total combinations = product of all list lengths.
"""

# -- Threshold scoring grid: 18 combinations --
# Focuses on entry selectivity and hard stop floor tuning.
# NOTE: hard_stop_atr_mult is irrelevant (1-min ATR too small, always hits floor)
GRID = {
    # Strategy: buy score threshold (higher = more selective entries)
    "strategies.0.config.score_buy_target": [2.0, 2.5, 3.0],

    # Strategy: minimum indicator agreement (higher = fewer but higher-conviction trades)
    "strategies.0.config.min_indicators_required": [3, 4],

    # Risk: hard stop floor (the actual stop level — ATR always clamps here at 1-min resolution)
    # Tested: 3% = best net P&L but 55% hard stop rate; 4.5% = balanced; 5.5% = worst net P&L but best win rate
    "risk.2.hard_stop_min_pct": [0.03, 0.045, 0.055],

    # Uncomment for deeper search:
    # "risk.2.trailing_activation_pct": [0.015, 0.02, 0.025],
    # "risk.2.hard_stop_max_pct": [0.06, 0.08, 0.10],
    # "strategies.0.config.score_sell_target": [1.5, 2.0, 2.5],
    # "strategies.0.config.cooldown_bars": [1, 2, 3],
    # "risk.2.max_hold_hours": [24, 48, 72],
    # "execution.default_notional": [50, 75, 100],
}
