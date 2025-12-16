# Strategy Performance Tracking System

**Created**: 2025-12-08
**Purpose**: Track bot configuration changes and correlate with performance for A/B testing and optimization

---

## Overview

This system enables you to:
1. **Track configuration changes** - Every time you adjust bot settings, create a snapshot
2. **Link trades to configurations** - Know exactly which settings produced which trades
3. **Compare strategies** - See which configurations perform best
4. **A/B test optimizations** - Test new settings against baseline

---

## Quick Start

### Step 1: Run Migration

```bash
# Apply the migration to create tables
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -f /path/to/002_create_strategy_snapshots_table.sql"
```

### Step 2: Initialize on Bot Startup

Add to your bot initialization code (e.g., in `sighook/main.py` or `webhook/main.py`):

```python
from sighook.strategy_snapshot_manager import StrategySnapshotManager

# After initializing config, logger, db
snapshot_mgr = StrategySnapshotManager(db, logger)
await snapshot_mgr.save_current_config(
    config,
    notes="Initial baseline - RSI weight 2.5, no min indicators"
)
```

### Step 3: Link Trades (Already Happens Automatically)

In `signal_manager.py`, after placing an order, link it to the current strategy:

```python
# In buy_sell_scoring() or after order placement
await self.snapshot_mgr.link_trade_to_strategy(
    order_id=order_id,
    signal_data={
        'Score': {'Buy Score': buy_score, 'Sell Score': sell_score},
        'trigger': trigger,
        'indicator_breakdown': {
            'RSI': rsi_contribution,
            'MACD': macd_contribution,
            # ... other indicators
        }
    }
)
```

### Step 4: Daily Summary (Automated)

Add a scheduled job (cron or scheduler) to run daily:

```python
# Run at 00:05 UTC daily
from datetime import date, timedelta
yesterday = date.today() - timedelta(days=1)
await snapshot_mgr.compute_daily_summary(yesterday)
```

---

## Example Workflow: Testing Settings Changes

### Scenario: Reduce RSI Weight from 2.5 → 1.5

**Before (Baseline)**:
```python
# config.json or .env
RSI_BUY_WEIGHT=2.5
RSI_SELL_WEIGHT=2.5
MIN_INDICATORS_REQUIRED=0  # No multi-indicator requirement
```

**Create baseline snapshot**:
```python
await snapshot_mgr.save_current_config(
    config,
    notes="Baseline: RSI weight 2.5, win rate 13.3%"
)
```

**Run for 7 days, collect data...**

---

**After (Optimization)**:
```python
# Updated config
RSI_BUY_WEIGHT=1.5  # ← Changed
RSI_SELL_WEIGHT=1.5  # ← Changed
MIN_INDICATORS_REQUIRED=2  # ← Added multi-indicator confirmation
```

**Create new snapshot**:
```python
await snapshot_mgr.save_current_config(
    config,
    notes="Test: RSI weight 1.5 + require 2 indicators"
)
```

**Run for 7 days, collect data...**

---

**Compare Results**:
```python
results = await snapshot_mgr.compare_strategies(limit=2)
for strategy in results:
    print(f"""
    Strategy: {strategy['notes']}
    Active: {strategy['active_from']} to {strategy['active_until'] or 'current'}
    Days: {strategy['days_active']}
    Total Trades: {strategy['total_trades']}
    Win Rate: {strategy['avg_win_rate']:.1f}%
    Total P&L: ${strategy['total_pnl']:.2f}
    Profit Factor: {strategy['avg_profit_factor']:.2f}
    Expectancy: ${strategy['avg_expectancy']:.2f}
    """)
```

**Expected Output**:
```
Strategy: Baseline: RSI weight 2.5, win rate 13.3%
Active: 2025-12-01 to 2025-12-08
Days: 7
Total Trades: 210
Win Rate: 13.3%
Total P&L: $5.18
Profit Factor: 1.19
Expectancy: $0.02

Strategy: Test: RSI weight 1.5 + require 2 indicators
Active: 2025-12-08 to current
Days: 7
Total Trades: 78
Win Rate: 29.5%
Total P&L: $18.45
Profit Factor: 2.15
Expectancy: $0.24
```

**Decision**: Keep the new settings! Win rate doubled, expectancy 12x better.

---

## Database Schema

### `strategy_snapshots` Table
Stores configuration snapshots.

| Column | Type | Description |
|--------|------|-------------|
| snapshot_id | UUID | Unique identifier |
| active_from | TIMESTAMPTZ | When config became active |
| active_until | TIMESTAMPTZ | When config was replaced (NULL = current) |
| score_buy_target | DECIMAL | Buy score threshold |
| indicator_weights | JSONB | {"Buy RSI": 2.5, ...} |
| rsi_buy_threshold | DECIMAL | RSI buy threshold (e.g., 25) |
| tp_threshold | DECIMAL | Take profit % |
| sl_threshold | DECIMAL | Stop loss % |
| cooldown_bars | INTEGER | Cooldown period |
| min_indicators_required | INTEGER | Multi-indicator confirmation |
| excluded_symbols | TEXT[] | Blacklisted symbols |
| config_hash | VARCHAR(64) | SHA-256 hash for deduplication |
| notes | TEXT | User description of this config |

---

### `strategy_performance_summary` Table
Daily aggregated performance per strategy.

| Column | Type | Description |
|--------|------|-------------|
| snapshot_id | UUID | Links to strategy_snapshots |
| date | DATE | Summary date |
| total_trades | INTEGER | Sell orders executed |
| winning_trades | INTEGER | Trades with P&L > $0.01 |
| losing_trades | INTEGER | Trades with P&L < -$0.01 |
| total_pnl_usd | DECIMAL | Net P&L for the day |
| win_rate | DECIMAL | % winning trades |
| profit_factor | DECIMAL | Gross profit / gross loss |
| expectancy_usd | DECIMAL | Avg P&L per trade |
| fast_exits_count | INTEGER | Trades held < 60s |

---

### `trade_strategy_link` Table
Links each trade to the strategy that generated it.

| Column | Type | Description |
|--------|------|-------------|
| order_id | VARCHAR | Coinbase order ID |
| snapshot_id | UUID | Strategy that generated this trade |
| buy_score | DECIMAL | Buy score at entry |
| sell_score | DECIMAL | Sell score at exit |
| trigger_type | VARCHAR | 'score', 'roc_momo', 'tp_sl' |
| indicators_fired | INTEGER | # indicators that fired |
| indicator_breakdown | JSONB | {"RSI": 2.5, "MACD": 1.8} |

---

## Querying Performance

### Get Current Active Strategy
```sql
SELECT * FROM current_strategy;
```

### Compare All Strategies
```sql
SELECT * FROM strategy_comparison
ORDER BY avg_win_rate DESC;
```

### Find Best Performing Strategy
```sql
SELECT
    notes,
    avg_win_rate,
    total_pnl,
    avg_profit_factor
FROM strategy_comparison
WHERE days_active >= 7  -- At least 1 week of data
ORDER BY avg_expectancy DESC
LIMIT 5;
```

### Analyze Fast Exits by Strategy
```sql
SELECT
    ss.notes,
    sps.fast_exits_count,
    sps.fast_exits_pnl,
    sps.fast_exits_count::float / NULLIF(sps.total_trades, 0) * 100 as fast_exit_pct
FROM strategy_performance_summary sps
JOIN strategy_snapshots ss ON ss.snapshot_id = sps.snapshot_id
WHERE sps.total_trades > 10  -- Minimum sample size
ORDER BY fast_exit_pct DESC;
```

### Daily Win Rate Trend for Current Strategy
```sql
SELECT
    date,
    total_trades,
    win_rate,
    total_pnl_usd
FROM strategy_performance_summary
WHERE snapshot_id = (SELECT snapshot_id FROM current_strategy)
ORDER BY date DESC
LIMIT 30;
```

---

## Integration with Daily Email Report

Add a "Strategy Performance" section to your email report:

```python
# In botreport/aws_daily_report.py
from sighook.strategy_snapshot_manager import StrategySnapshotManager

async def get_strategy_comparison_section():
    """Generate strategy comparison HTML for email report."""
    snapshot_mgr = StrategySnapshotManager(db, logger)
    strategies = await snapshot_mgr.compare_strategies(limit=3)

    html = "<h3>Strategy Comparison (Last 3 Configs)</h3>"
    html += "<table border='1'>"
    html += "<tr><th>Strategy</th><th>Days</th><th>Trades</th><th>Win%</th><th>P&L</th><th>Expectancy</th></tr>"

    for s in strategies:
        html += f"<tr>"
        html += f"<td>{s['notes'] or 'Unnamed'}</td>"
        html += f"<td>{s['days_active']}</td>"
        html += f"<td>{s['total_trades']}</td>"
        html += f"<td>{s['avg_win_rate']:.1f}%</td>"
        html += f"<td>${s['total_pnl']:.2f}</td>"
        html += f"<td>${s['avg_expectancy']:.3f}</td>"
        html += f"</tr>"

    html += "</table>"
    return html
```

---

## Rollback to Previous Configuration

If a new configuration performs poorly:

```python
# Find previous configuration
async with db.async_session() as session:
    query = text("""
        SELECT
            snapshot_id,
            notes,
            indicator_weights,
            rsi_buy_threshold,
            min_indicators_required
        FROM strategy_snapshots
        WHERE active_until IS NOT NULL
        ORDER BY active_until DESC
        LIMIT 1
    """)
    result = await session.execute(query)
    prev_config = result.fetchone()

# Manually update your .env or config.json with previous values
# Then save snapshot
await snapshot_mgr.save_current_config(
    config,
    notes=f"Rollback to: {prev_config.notes}"
)
```

---

## Best Practices

1. **Always add notes** when saving a snapshot:
   ```python
   await snapshot_mgr.save_current_config(
       config,
       notes="Increased TP to 3.5%, SL to -2.5% - testing wider targets"
   )
   ```

2. **Test for minimum 7 days** - Too short = not statistically significant

3. **Change ONE thing at a time** - Makes it clear what worked/failed

4. **Track key metrics**:
   - Win rate (target: >25%)
   - Profit factor (target: >1.5)
   - Expectancy (target: >$0.10)
   - Fast exits (target: <5% of trades)

5. **Review weekly** - Compare current week vs baseline

---

## Common A/B Tests to Run

### Test 1: Multi-Indicator Confirmation
**Hypothesis**: Requiring 2+ indicators reduces false signals

| Config | min_indicators_required | Expected Win Rate |
|--------|------------------------|------------------|
| Baseline | 0 | 13% |
| Test A | 2 | 25-30% |
| Test B | 3 | 35-40% (fewer trades) |

### Test 2: RSI Weight Reduction
**Hypothesis**: RSI is over-dominating, reduce its influence

| Config | RSI Weight | Expected Contribution |
|--------|-----------|----------------------|
| Baseline | 2.5 | 75% |
| Test A | 1.5 | 45-50% |
| Test B | 1.0 | 30-35% |

### Test 3: Symbol Blacklisting
**Hypothesis**: Some symbols are consistent losers

| Config | Excluded Symbols | Expected P&L Improvement |
|--------|-----------------|-------------------------|
| Baseline | [] | $0.74/day |
| Test A | [A8-USD, PENGU-USD] | $3-4/day (+300%) |

### Test 4: Wider TP/SL
**Hypothesis**: Stops too tight, getting hit by noise

| Config | TP% | SL% | Expected Win Rate |
|--------|-----|-----|------------------|
| Baseline | 3.0 | -2.0 | 13% |
| Test A | 3.5 | -2.5 | 18-22% |
| Test B | 4.0 | -3.0 | 22-28% |

---

## Troubleshooting

### Snapshot not creating
Check logs for errors:
```bash
ssh bottrader-aws "docker logs webhook 2>&1 | grep 'strategy snapshot'"
```

### Trades not linking to strategy
Verify `trade_strategy_link` table:
```sql
SELECT COUNT(*) FROM trade_strategy_link
WHERE created_at > NOW() - INTERVAL '24 hours';
```

Should match number of recent trades.

### Daily summary not computing
Run manually:
```python
from datetime import date
await snapshot_mgr.compute_daily_summary(date(2025, 12, 8))
```

---

## Next Steps

1. **Run migration** to create tables
2. **Initialize on bot startup** to create baseline snapshot
3. **Run for 1 week** to collect baseline data
4. **Implement recommendations** from performance analysis
5. **Create new snapshot** with notes about changes
6. **Compare after 1 week** using `compare_strategies()`

---

**Questions?** Check:
- `sighook/strategy_snapshot_manager.py` - Implementation
- `database/migrations/002_create_strategy_snapshots_table.sql` - Schema
- Email reports - Will show strategy comparison section

