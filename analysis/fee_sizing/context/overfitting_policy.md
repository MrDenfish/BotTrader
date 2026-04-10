# Overfitting Policy & Guardrails

## CRITICAL: All proposed strategy/parameter changes MUST be evaluated for overfitting risk BEFORE implementation.

### Evaluation Checklist
1. Is this change derived from **structural reasoning** (fee math, market microstructure) or from **backtest performance**?
2. If backtest-derived: was it validated on **out-of-sample data** (different time period or different symbols)?
3. Does the change add a new threshold/filter tuned to a specific dataset?
4. Could the same improvement be explained by random variation in the test data?

### Known Potentially Overfit Changes (all tuned to same 180-day, 9-symbol dataset)
These 5 changes were made based on a single backtest window. They should be **re-validated on new out-of-sample datasets** before being considered permanent:

1. **Trend confirmation gate** (`require_trend_for_buy: true`) — Added because Touch+RSI+VolDiv pattern caused 12/18 hard stops in the 139-trade backtest. May or may not generalize.
2. **roc_momo_24h disabled** (`enable_roc_24h_momentum: false`) — Disabled due to 28% hard stop rate in paper trading + backtest. Might work in different market regimes.
3. **Soft stops disabled** (`soft_stop_enabled: false`) — Disabled as biggest P&L drag in backtest. May be useful with different threshold or in different volatility regimes.
4. **Hard stop floor set to 4.5%** (`hard_stop_min_pct: 0.045`) — Selected from 3-way comparison (3%, 4.5%, 5.5%) on the same dataset.
5. **Trailing activation lowered to 2%** (`trailing_activation_pct: 0.02`) — Changed from 3% based on backtest tuning.

### Policy: Do NOT revert these changes yet
- Reverting without new data to test against just returns to an untested state
- Instead: validate each against new out-of-sample datasets as they become available
- If a change holds across 2+ independent datasets, it's likely a real improvement
- If it only helped on the original dataset, revert it

### Low Overfitting Risk Changes (safe to implement)
- Fee-aware math corrections (already implemented in exit_manager.py)
- Conviction-based position sizing (more on high-indicator-count entries)
- Volume/spread filters derived from market microstructure
- Any change derived from exchange fee schedules, not price patterns

### Dataset Requirements for Validation
- Need multiple independent 180-day datasets with different symbols
- Current dataset: 9 symbols (BTC, ETH, SOL, XRP, ADA, DOGE, LINK, DOT, AVAX), ~Sep 2025 – Mar 2026
- New datasets should use different symbols and/or different time periods
- See memory/datasets.md for dataset acquisition plan
