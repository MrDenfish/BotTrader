"""Grid search parameter definitions for composite scoring strategy.

Each key is a dotted path into the YAML config dict.
Values are lists of values to test.

Total combinations = product of all list lengths.
Keep grids small for initial exploration (~20-50 runs),
then narrow down and expand promising regions.
"""

# -- Initial exploration grid: 48 combinations --
# Focuses on the most impactful params: scoring thresholds,
# indicator count, stop losses, and position sizing.
GRID = {
    # Strategy: buy score threshold (how many indicator confirmations needed)
    "strategies.0.config.score_buy_target": [1.5, 2.0, 2.5, 3.0],

    # Strategy: minimum indicator agreement
    "strategies.0.config.min_indicators_required": [2, 3, 4],

    # Risk: hard stop loss
    "risk.1.hard_stop_pct": [0.03, 0.045, 0.06],

    # Removed to keep grid small. Uncomment for deeper search:
    # "strategies.0.config.cooldown_bars": [1, 2, 3],
    # "risk.1.soft_stop_pct": [0.02, 0.03, 0.04],
    # "risk.1.trailing_activation_pct": [0.02, 0.03, 0.04],
    # "risk.1.max_hold_hours": [24, 48, 72],
    # "execution.default_notional": [30, 50, 75],
}
