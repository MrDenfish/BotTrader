# Dynamic Symbol Filter - Complete Documentation

## Overview

The **Dynamic Symbol Filter** is a data-driven system that automatically excludes/includes symbols based on rolling performance metrics. It replaces the hardcoded exclusion lists with an intelligent, adaptive approach that responds to market conditions.

## Problem Solved

**Before:** You had to manually:
1. Identify poor-performing symbols through data analysis
2. Edit `.env` on the server to add symbols to `EXCLUDED_SYMBOLS`
3. Edit `sighook/trading_strategy.py` to update hardcoded fallback list
4. Redeploy code
5. Remember to re-evaluate symbols periodically
6. Manually remove symbols when they improve

**After:** The system automatically:
1. Evaluates all symbols daily based on performance metrics
2. Excludes poor performers (low win rate, high losses, wide spreads)
3. Re-includes symbols when performance improves
4. Maintains permanent exclusions for manually blacklisted symbols
5. Caches results for performance (1-hour TTL)
6. Provides detailed logging of exclusion/inclusion changes

## How It Works

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  DynamicSymbolFilter                        │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Performance-Based Exclusions (Database Query)        │  │
│  │ - Win rate < 30%                                     │  │
│  │ - Avg P&L < -$5                                      │  │
│  │ - Total P&L < -$50                                   │  │
│  └──────────────────────────────────────────────────────┘  │
│                          ▼                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Spread-Based Exclusions (Live Market Data)          │  │
│  │ - Bid-Ask spread > 2%                                │  │
│  └──────────────────────────────────────────────────────┘  │
│                          ▼                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Permanent Exclusions (Manual Override)               │  │
│  │ - PERMANENT_EXCLUSIONS from .env                     │  │
│  └──────────────────────────────────────────────────────┘  │
│                          ▼                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Final Exclusion List (Cached 1 hour)                 │  │
│  │ - Union of all above                                 │  │
│  │ - Logged when changes occur                          │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                          ▼
        ┌─────────────────┴─────────────────┐
        ▼                                   ▼
┌──────────────────┐            ┌──────────────────┐
│ TradingStrategy  │            │ PassiveOrderMgr  │
│ (sighook)        │            │ (webhook)        │
│                  │            │                  │
│ Skips excluded   │            │ Skips excluded   │
│ symbols during   │            │ symbols before   │
│ signal generation│            │ placing passive  │
└──────────────────┘            └──────────────────┘
```

### Evaluation Criteria

Symbols are **automatically excluded** if they meet **ANY** of the following criteria over the lookback period (default 30 days):

| Criterion | Default Threshold | Description |
|-----------|------------------|-------------|
| **Win Rate** | < 30% | Percentage of profitable trades |
| **Avg P&L** | < -$5 | Average profit/loss per trade |
| **Total P&L** | < -$50 | Total net profit/loss |
| **Avg Spread** | > 2% | Average bid-ask spread percentage |
| **Min Trades** | ≥ 5 | Minimum trades for statistical significance |

**Example:** TNSR-USD with win_rate=25%, avg_pnl=-$0.15, total_pnl=-$0.155 would be excluded for low win rate.

Symbols are **automatically re-included** when they meet **ALL** threshold requirements (performance improves).

### Permanent Exclusions

The `PERMANENT_EXCLUSIONS` env variable allows manual override. Symbols in this list will **NEVER** be auto-included, regardless of performance.

**Use cases:**
- HODL coins you don't want to trade
- SHILL coins for sell-only strategies
- Broken/delisted symbols
- Regulatory restrictions

## Installation & Configuration

### Step 1: Add Configuration to `.env`

Add these lines to your `/opt/bot/.env` file on the server:

```env
# ---------- Dynamic Symbol Filter ----------
DYNAMIC_FILTER_ENABLED=true
DYNAMIC_FILTER_MIN_WIN_RATE=0.30
DYNAMIC_FILTER_MIN_AVG_PNL=-5.0
DYNAMIC_FILTER_MIN_TOTAL_PNL=-50.0
DYNAMIC_FILTER_MAX_SPREAD_PCT=0.02
DYNAMIC_FILTER_MIN_TRADES=5
DYNAMIC_FILTER_LOOKBACK_DAYS=30
PERMANENT_EXCLUSIONS=
```

See `.env.dynamic_filter_example` for detailed parameter descriptions.

### Step 2: Deploy Code

The code changes are already committed. Deploy with:

```bash
ssh bottrader-aws
cd /opt/bot
./update.sh
```

### Step 3: Verify Configuration

```bash
ssh bottrader-aws "docker exec webhook python3 -c \"
import os
print('Dynamic Filter Enabled:', os.getenv('DYNAMIC_FILTER_ENABLED'))
print('Min Win Rate:', os.getenv('DYNAMIC_FILTER_MIN_WIN_RATE'))
print('Min Avg PNL:', os.getenv('DYNAMIC_FILTER_MIN_AVG_PNL'))
print('Lookback Days:', os.getenv('DYNAMIC_FILTER_LOOKBACK_DAYS'))
print('Permanent Exclusions:', os.getenv('PERMANENT_EXCLUSIONS'))
\""
```

### Step 4: Monitor Logs

Watch for dynamic filter activity:

```bash
ssh bottrader-aws "docker logs -f webhook 2>&1 | grep -E 'dynamic.*filter|Newly excluded|Newly included|dynamically excluded'"
```

Expected log messages:
- `Dynamic Symbol Filter initialized: enabled=True, min_win_rate=30.0%, ...`
- `🚫 Newly excluded symbols: ['TNSR-USD', 'A8-USD']`
- `✅ Newly included symbols: ['XYZ-USD']`
- `⛔ Skipping TNSR-USD — dynamically excluded (poor performance)`

## Usage

### Check Current Exclusions

```python
# In code
excluded = await dynamic_filter.get_excluded_symbols()
print(f"Currently excluded: {sorted(excluded)}")
```

```bash
# Via database query
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT
    symbol,
    COUNT(*) as trades,
    SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END)::float / COUNT(*) as win_rate,
    AVG(pnl_usd) as avg_pnl,
    SUM(pnl_usd) as total_pnl
FROM trade_records
WHERE order_time >= NOW() - INTERVAL '30 days'
  AND pnl_usd IS NOT NULL
GROUP BY symbol
HAVING COUNT(*) >= 5
ORDER BY total_pnl ASC
LIMIT 20;
\""
```

### Get Performance for Specific Symbol

```python
performance = await dynamic_filter.get_symbol_performance('TNSR-USD')
if performance:
    print(f"Symbol: {performance['symbol']}")
    print(f"Trade Count: {performance['trade_count']}")
    print(f"Win Rate: {performance['win_rate']:.1%}")
    print(f"Avg P&L: ${performance['avg_pnl']:.2f}")
    print(f"Total P&L: ${performance['total_pnl']:.2f}")
    print(f"Excluded: {performance['is_excluded']}")
```

### Get Detailed Exclusion Report

```python
report = await dynamic_filter.get_exclusion_report()
print(f"Performance-based: {report['performance']}")
print(f"Spread-based: {report['spread']}")
print(f"Permanent: {report['permanent']}")
print(f"Total excluded: {len(report['total'])}")
```

### Force Refresh (Bypass Cache)

```python
# Force immediate re-evaluation
excluded = await dynamic_filter.force_refresh()
```

## Tuning & Optimization

### Conservative Settings (More Exclusions)

```env
DYNAMIC_FILTER_MIN_WIN_RATE=0.40        # Require 40% win rate
DYNAMIC_FILTER_MIN_AVG_PNL=-2.0         # Max -$2 avg loss
DYNAMIC_FILTER_MIN_TOTAL_PNL=-20.0      # Max -$20 total loss
DYNAMIC_FILTER_MAX_SPREAD_PCT=0.015     # Max 1.5% spread
DYNAMIC_FILTER_MIN_TRADES=10            # Require 10 trades
```

**Effect:** Fewer symbols traded, higher quality selection, lower risk

### Aggressive Settings (Fewer Exclusions)

```env
DYNAMIC_FILTER_MIN_WIN_RATE=0.20        # Accept 20% win rate
DYNAMIC_FILTER_MIN_AVG_PNL=-10.0        # Accept -$10 avg loss
DYNAMIC_FILTER_MIN_TOTAL_PNL=-100.0     # Accept -$100 total loss
DYNAMIC_FILTER_MAX_SPREAD_PCT=0.03      # Accept 3% spread
DYNAMIC_FILTER_MIN_TRADES=3             # Require only 3 trades
```

**Effect:** More symbols traded, lower quality bar, higher risk

### Recommended Settings (Balanced)

```env
DYNAMIC_FILTER_MIN_WIN_RATE=0.30        # Default: 30% win rate
DYNAMIC_FILTER_MIN_AVG_PNL=-5.0         # Default: -$5 avg loss
DYNAMIC_FILTER_MIN_TOTAL_PNL=-50.0      # Default: -$50 total loss
DYNAMIC_FILTER_MAX_SPREAD_PCT=0.02      # Default: 2% spread
DYNAMIC_FILTER_MIN_TRADES=5             # Default: 5 trades
DYNAMIC_FILTER_LOOKBACK_DAYS=30         # Default: 30 days
```

## Performance Impact

### Computational Cost

- **Database Query:** ~50-100ms (cached for 1 hour)
- **Spread Check:** ~5-10ms (uses in-memory market data)
- **Cache Lookup:** <1ms (99% of checks)
- **Total Overhead:** Negligible (<0.1% of trading cycle)

### Memory Usage

- **Cache Size:** ~1-5 KB (list of 20-50 symbol strings)
- **Per-Symbol Metadata:** None (excluded set only)
- **Total:** <10 KB

### Database Impact

- **Queries per Hour:** 1 (when cache expires)
- **Query Complexity:** Simple GROUP BY with aggregations
- **Index Usage:** Uses `order_time` index on `trade_records`
- **Load:** Minimal

## Monitoring & Alerts

### Key Metrics to Track

1. **Exclusion Count Over Time**
   - Chart: Number of excluded symbols per day
   - Alert: If count > 50 (too many exclusions)

2. **Exclusion Churn**
   - Chart: Newly excluded + newly included per day
   - Alert: If churn > 10 symbols/day (unstable thresholds)

3. **Performance Improvement**
   - Chart: Overall P&L before vs. after dynamic filtering
   - Target: +10% improvement in 30 days

4. **False Negatives**
   - Chart: Profitable trades in excluded symbols
   - Alert: If missing >$100/day in profits

### Database Monitoring Query

```sql
-- Daily exclusion report
WITH excluded_symbols AS (
    SELECT symbol
    FROM trade_records
    WHERE order_time >= NOW() - INTERVAL '30 days'
      AND pnl_usd IS NOT NULL
    GROUP BY symbol
    HAVING COUNT(*) >= 5
       AND (
           SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END)::float / COUNT(*) < 0.30
           OR AVG(pnl_usd) < -5.0
           OR SUM(pnl_usd) < -50.0
       )
)
SELECT
    COUNT(*) as total_excluded,
    ARRAY_AGG(symbol ORDER BY symbol) as excluded_list
FROM excluded_symbols;
```

## Troubleshooting

### Issue: Too Many Symbols Excluded

**Symptoms:** >50% of symbols excluded, limited trading opportunities

**Diagnosis:**
```bash
# Check current exclusions
ssh bottrader-aws "docker exec webhook python3 -c \"
import asyncio
from Shared_Utils.dynamic_symbol_filter import DynamicSymbolFilter
# ... get report ...
\""
```

**Solutions:**
1. Relax thresholds (lower min_win_rate, higher min_total_pnl)
2. Reduce lookback period (use 14 days instead of 30)
3. Increase min_trades (require more data for exclusion)

### Issue: No Symbols Excluded (Filter Not Working)

**Symptoms:** Exclusion list is empty, obvious losers still trading

**Diagnosis:**
```bash
# Check if filter is enabled
docker exec webhook env | grep DYNAMIC_FILTER

# Check logs for errors
docker logs webhook 2>&1 | grep -i "dynamic.*filter.*error"
```

**Solutions:**
1. Verify `DYNAMIC_FILTER_ENABLED=true` in .env
2. Check database connectivity
3. Verify minimum trade threshold isn't too high
4. Check logs for initialization errors

### Issue: Symbol Should Be Excluded But Isn't

**Symptoms:** Known poor performer not in exclusion list

**Diagnosis:**
```python
# Check symbol performance
performance = await dynamic_filter.get_symbol_performance('SYMBOL-USD')
print(performance)

# Force refresh to bypass cache
excluded = await dynamic_filter.force_refresh()
print('SYMBOL-USD' in excluded)
```

**Solutions:**
1. Check if symbol has minimum required trades
2. Verify thresholds aren't too lenient
3. Check if symbol is within lookback period
4. Force cache refresh

### Issue: Symbol Should Be Included But Isn't

**Symptoms:** Performing symbol still excluded

**Diagnosis:**
```bash
# Check if in permanent exclusions
docker exec webhook env | grep PERMANENT_EXCLUSIONS

# Check recent performance
docker exec db psql -U bot_user -d bot_trader_db -c "
SELECT * FROM trade_records
WHERE symbol = 'SYMBOL-USD'
  AND order_time >= NOW() - INTERVAL '30 days'
ORDER BY order_time DESC;
"
```

**Solutions:**
1. Check if symbol is in `PERMANENT_EXCLUSIONS`
2. Verify performance meets ALL thresholds (not just some)
3. Check leaderboard filter (separate from dynamic filter)
4. Force cache refresh

## Fallback Behavior

If the dynamic filter fails for any reason, the system **gracefully falls back** to:

1. **TradingStrategy:** Uses `_fallback_excluded_symbols` list (hardcoded)
2. **PassiveOrderManager:** Continues without dynamic filtering
3. **Logs Warning:** "Dynamic filter failed, using fallback list"

This ensures **zero downtime** even if database is unavailable.

## Migration from Static Lists

### Old Approach
```python
# sighook/trading_strategy.py
self.excluded_symbols = [
    'A8-USD', 'PENGU-USD', 'TNSR-USD', ...
]
```

### New Approach
```python
# Automatic, data-driven
excluded = await self.dynamic_filter.get_excluded_symbols()
# Returns: {'TNSR-USD', 'A8-USD'} based on actual performance
```

### Compatibility

- **Old code still works:** Fallback list used if filter disabled
- **Gradual migration:** Can disable filter temporarily with `DYNAMIC_FILTER_ENABLED=false`
- **Zero breaking changes:** Existing functionality preserved

## Future Enhancements

### Planned Features

1. **Symbol Scoring System**
   - Assign quality scores (0-100) to each symbol
   - Trade higher-scoring symbols more aggressively
   - Gradually phase out poor performers

2. **Multi-Timeframe Analysis**
   - Evaluate performance at 7d, 14d, 30d, 90d horizons
   - Weight recent performance more heavily
   - Detect improving/deteriorating trends

3. **Strategy-Specific Filtering**
   - Different thresholds for passive MM vs. active trading
   - Different thresholds for momentum vs. mean-reversion
   - Per-strategy exclusion lists

4. **Volatility-Based Filtering**
   - Exclude low-volatility symbols from active trading
   - Exclude high-volatility symbols from passive MM
   - Dynamic adaptation to market conditions

5. **Machine Learning Integration**
   - Predict symbol performance using ML models
   - Proactively exclude symbols before they deteriorate
   - Identify symbols about to improve

## API Reference

### `DynamicSymbolFilter`

```python
class DynamicSymbolFilter:
    def __init__(self, shared_data_manager, config, logger_manager=None)
    async def get_excluded_symbols(self, force_refresh: bool = False) -> Set[str]
    async def get_symbol_performance(self, symbol: str) -> Optional[Dict]
    async def get_exclusion_report(self) -> Dict[str, List[str]]
    async def force_refresh() -> Set[str]
    def is_excluded(self, symbol: str, cached: bool = True) -> bool
```

### Configuration Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `DYNAMIC_FILTER_ENABLED` | bool | `true` | Enable/disable filtering |
| `DYNAMIC_FILTER_MIN_WIN_RATE` | float | `0.30` | Minimum win rate (30%) |
| `DYNAMIC_FILTER_MIN_AVG_PNL` | float | `-5.0` | Minimum avg P&L per trade |
| `DYNAMIC_FILTER_MIN_TOTAL_PNL` | float | `-50.0` | Minimum total P&L |
| `DYNAMIC_FILTER_MAX_SPREAD_PCT` | float | `0.02` | Maximum avg spread (2%) |
| `DYNAMIC_FILTER_MIN_TRADES` | int | `5` | Minimum trades required |
| `DYNAMIC_FILTER_LOOKBACK_DAYS` | int | `30` | Analysis lookback period |
| `PERMANENT_EXCLUSIONS` | str | `""` | Comma-separated permanent exclusions |

---

**Created:** Dec 15, 2025
**Version:** 1.0.0
**Maintainer:** BotTrader Team
**Related:** `PASSIVE_MM_FIXES_SESSION.md`, `Shared_Utils/dynamic_symbol_filter.py`
