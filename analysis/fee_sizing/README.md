# BotTrader Fee/Sizing Analysis Bundle

## The Problem

BotTrader is a crypto trading bot running on Kraken (paper trading). The strategy is **validated as robust** (not overfit) via 3-set out-of-sample testing across 27 symbols. However, **every set is net negative after fees** despite being gross profitable.

Fee drag is now the #1 P&L lever. The strategy logic is validated — the question is sizing and fee economics.

## Key Numbers

- **Kraken fees**: 0.25% maker / 0.40% taker (0.65% round-trip)
- **Current notional**: $75 per trade (score trigger), $30 for momentum triggers
- **Avg winning trade**: $1.47–$1.80 gross
- **Fee per round-trip at $75**: ~$0.49
- **Fee drag per 100 trades**: ~$50
- **Starting capital**: $10,000 (paper)

### Backtest Results (aligned config, 180-day window each)

| Set | Symbols | Trades | Win Rate | Gross P&L | Fees | Net P&L | Profit Factor |
|-----|---------|--------|----------|-----------|------|---------|---------------|
| A (training) | BTC, ETH, SOL, XRP, DOGE, ADA, LINK, DOT, AVAX | 103 | 54.4% | ~-$12 | ~$50 | -$62 | 0.57 |
| B (OOS) | AAVE, APT, ATOM, BNB, FIL, ICP, LTC, NEAR, UNI | 113 | 64.6% | ~$27 | ~$49 | -$22 | 0.85 |
| C (OOS) | ALGO, ARB, FET, HBAR, INJ, LDO, OP, SUI, TAO | 99 | 61.6% | ~$17 | ~$50 | -$33 | 0.77 |

## What I Need Analyzed

1. **Notional sensitivity**: Model P&L at $100, $150, $200, $300 notional using the actual win/loss distributions from the JSONL trade files. At what notional does the strategy become net profitable?

2. **Trade quality filter**: Are there subsets of trades (by exit reason, indicator count, conviction level) that are net profitable even at $75? Could we reduce frequency and only take the best setups?

3. **Fee tier analysis**: Kraken offers lower fees at higher 30-day volume. What volume tier would we need to reach for the strategy to break even? Is it achievable?

4. **Exit reason breakdown**: Which exit categories (trailing_stop, hard_stop, stale_exit, peak_time_limit) are net profitable vs. net negative after fees? Where is the biggest drag?

5. **Risk/reward**: Given the win rate and avg win/loss, what's the Kelly criterion suggesting for optimal sizing?

## File Structure

```
fee_sizing_bundle/
  README.md                          -- This file
  config/
    kraken_paper_trading.yaml        -- Live paper trading config (fees, sizing, triggers)
    backtest_composite.yaml          -- Backtest config (should match production)
  backtest_trades/
    set_a_diagnostic_trades.jsonl    -- Training set: 103 trades with full metadata
    set_b_diagnostic_trades.jsonl    -- OOS set B: 113 trades
    set_c_diagnostic_trades.jsonl    -- OOS set C: 99 trades
    set_a_backtest.log               -- Full backtest log with per-symbol summaries
  code/
    maker_only.py                    -- Execution plugin: sizing logic (notional_by_trigger)
    exit_manager.py                  -- Exit/risk plugin: fee-aware P&L, stop logic
    composite_scoring_config.py      -- Strategy config: thresholds, trigger settings
  context/
    overfitting_policy.md            -- Overfitting guardrails and validation results
    open_work_items.md               -- Current priorities (fee/sizing is P1)
```

## JSONL Trade Data Schema

Each line in the diagnostic_trades JSONL files is a JSON object with these key fields:

- `symbol` — Trading pair (e.g., "BTC-USD")
- `pnl` — Dollar P&L for the trade
- `pnl_pct` — Percentage P&L
- `exit_reason` — Why the trade closed (trailing_stop, hard_stop, stale_exit, peak_time_limit, etc.)
- `mfe_pct` — Maximum Favorable Excursion (best unrealized % gain)
- `mae_pct` — Maximum Adverse Excursion (worst unrealized % loss)
- `bars_held` — Duration in 1-minute bars (divide by 60 for hours)
- `entry_metadata` — Contains `score_components` (which indicators fired), `regime`, `indicator_snapshot`
- `exit_metadata` — Exit decision details
- `entry_price`, `exit_price`, `qty`, `entry_time`, `exit_time`

## Sizing Logic (from maker_only.py)

The notional (dollar amount per trade) is determined by priority:
1. Signal-level `qty` (if provided)
2. Signal-level `notional` (if provided)
3. `notional_by_trigger` config map (per trigger type: score, roc_momo_20m, roc_momo_24h)
4. `default_notional` fallback

Current config: `notional_by_trigger: {score: 75, roc_momo_20m: 30, roc_momo_24h: 30}`

## Fee Structure (Kraken)

| 30-Day Volume | Maker | Taker |
|--------------|-------|-------|
| $0 - $50K | 0.25% | 0.40% |
| $50K - $100K | 0.20% | 0.35% |
| $100K - $250K | 0.14% | 0.24% |
| $250K - $500K | 0.12% | 0.22% |
| $500K - $1M | 0.10% | 0.20% |

Current bot volume: ~100 trades/month x $75 = ~$7,500/month (bottom tier).

## Constraints

- Paper trading capital: $10,000
- Max position size should stay reasonable (don't want >5% of capital in one trade)
- Strategy generates ~100 trades per 180 days (~0.5/day) at current config
- Reducing trade frequency (higher conviction filter) is acceptable
- The strategy is validated — do NOT suggest changing entry/exit logic, only sizing and filtering
