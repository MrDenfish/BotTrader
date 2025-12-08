# Strategy Optimization Session - 2025-12-08

**Session Start**: 2025-12-08
**Branch**: `feature/strategy-optimization` (off main)
**Status**: IN PROGRESS

---

## Session Goals

### Primary Objective
Optimize bot trading performance by implementing recommended strategy tweaks based on performance analysis showing 13.3% win rate.

### Specific Tasks
1. ✅ Merge `bugfix/single-fifo-engine` → `main`
2. ⏳ Create `feature/strategy-optimization` branch
3. ⏳ Implement strategy tweaks:
   - Reduce RSI weight from 2.5 → 1.5
   - Add `min_indicators_required = 2` for multi-indicator confirmation
   - Blacklist A8-USD and PENGU-USD (consistent losers)
4. ⏳ Create performance tracking database tables
5. ⏳ Initialize baseline snapshot
6. ⏳ Deploy and monitor for 7 days

---

## Context from Previous Session

### Performance Issues Identified (2025-12-08 Report)

| Metric | Current Value | Issue |
|--------|--------------|-------|
| Win Rate | 13.3% (4/30) | 🔴 CRITICALLY LOW |
| Profit Factor | 1.19 | ⚠️ Barely profitable |
| Max Drawdown | 29.9% | 🔴 VERY HIGH |
| Fast Exits (<60s) | 7 trades, -$0.37 | Fee bleeding |
| Worst Performer | PENGU-USD: 3 trades, -$2.29, 0% win | Consistent loser |

### Root Causes Identified
1. **RSI Dominating** - 74.8% of buy signals, 69.3% of sell signals
   - Weight too high (2.5 vs others at 1.2-2.0)
   - Thresholds too loose (25/75 vs recommended 20/80)
2. **No Multi-Indicator Confirmation** - Single indicator can trigger trades
3. **High-Spread Symbols** - A8-USD (0.74% spread) bleeding fees
4. **Poor Symbol Selection** - PENGU-USD consistently losing

### Expected Improvements

| Metric | Baseline | Target | Improvement |
|--------|----------|--------|-------------|
| Win Rate | 13.3% | 28-35% | +115% |
| Trade Frequency | 30/day | 12-15/day | -50% |
| Profit Factor | 1.19 | 1.8-2.2 | +60% |
| Max Drawdown | 29.9% | <15% | -50% |

---

## Implementation Plan

### Phase 1: Reduce RSI Dominance ✅ (Priority 1)

**Current Settings** (.env or config.json):
```bash
# Indicator Weights
RSI_BUY_WEIGHT=2.5
RSI_SELL_WEIGHT=2.5
MACD_WEIGHT=1.8
TOUCH_WEIGHT=1.5
RATIO_WEIGHT=1.2
ROC_WEIGHT=2.0

# RSI Thresholds
RSI_BUY_THRESHOLD=25.0
RSI_SELL_THRESHOLD=75.0
```

**New Settings**:
```bash
# Reduce RSI influence
RSI_BUY_WEIGHT=1.5  # ← Changed from 2.5
RSI_SELL_WEIGHT=1.5  # ← Changed from 2.5

# Tighten RSI thresholds (more selective)
RSI_BUY_THRESHOLD=20.0  # ← Changed from 25.0 (only extreme oversold)
RSI_SELL_THRESHOLD=80.0  # ← Changed from 75.0 (only extreme overbought)
```

**Expected Impact**: Reduce RSI fires by ~50%, allowing MACD, ROC, Touch to contribute more.

---

### Phase 2: Add Multi-Indicator Confirmation (Priority 1)

**Implementation**: Update `sighook/signal_manager.py`

**Location**: After line 394 in `buy_sell_scoring()` method

**Code to Add**:
```python
# After computing buy_signal and sell_signal (line 394)

# Count how many indicators actually fired
buy_indicators_fired = sum(
    1 for ind in self.strategy_weights.keys()
    if ind.startswith("Buy") and last_row.get(ind, (0,))[0] == 1
)
sell_indicators_fired = sum(
    1 for ind in self.strategy_weights.keys()
    if ind.startswith("Sell") and last_row.get(ind, (0,))[0] == 1
)

MIN_INDICATORS_REQUIRED = 2  # Require at least 2 indicators to agree

# Suppress signal if insufficient indicators
if buy_signal[0] == 1 and buy_indicators_fired < MIN_INDICATORS_REQUIRED:
    guardrail_note = f"buy_suppressed_insufficient_indicators_{buy_indicators_fired}"
    buy_signal = (0, buy_signal[1], buy_signal[2])

if sell_signal[0] == 1 and sell_indicators_fired < MIN_INDICATORS_REQUIRED:
    guardrail_note = f"sell_suppressed_insufficient_indicators_{sell_indicators_fired}"
    sell_signal = (0, sell_signal[1], sell_signal[2])
```

**Alternative**: Add to config.json
```json
{
  "min_indicators_required": 2
}
```

**Expected Impact**: Reduce false signals by 60%, increase win rate from 13% to 25-30%.

---

### Phase 3: Symbol Blacklist (Priority 2)

**Implementation**: Update `.env` or `config.json`

**Option A - .env**:
```bash
# Symbol Filters
EXCLUDED_SYMBOLS=A8-USD,PENGU-USD
MAX_SPREAD_PCT=0.50  # 0.5% max spread (excludes A8-USD's 0.74%)
```

**Option B - config.json**:
```json
{
  "excluded_symbols": ["A8-USD", "PENGU-USD"],
  "max_spread_pct": 0.50
}
```

**Rationale**:
- A8-USD: 0.74% spread = $0.80 fee per $100 roundtrip
- PENGU-USD: 3 trades, -$2.29, 0% win rate (88% of today's losses)

**Expected Impact**: Save ~$2.50/day by avoiding consistent losers.

---

### Phase 4: Performance Tracking Setup (Priority 3)

**Database Migration**:
```bash
# Copy migration to AWS
scp database/migrations/002_create_strategy_snapshots_table.sql bottrader-aws:/tmp/

# Run migration
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -f /tmp/002_create_strategy_snapshots_table.sql"
```

**Creates 3 Tables**:
1. `strategy_snapshots` - Config snapshots
2. `strategy_performance_summary` - Daily performance metrics
3. `trade_strategy_link` - Links trades to configs

**Creates 2 Views**:
1. `current_strategy` - Active configuration
2. `strategy_comparison` - Performance comparison

---

### Phase 5: Initialize Baseline Snapshot

**Code Addition** (sighook/main.py or webhook/main.py):
```python
from sighook.strategy_snapshot_manager import StrategySnapshotManager

# After initializing config, logger, db
snapshot_mgr = StrategySnapshotManager(db, logger)
await snapshot_mgr.save_current_config(
    config,
    notes="Baseline: RSI weight 1.5, min_indicators=2, blacklist A8/PENGU"
)
```

**Purpose**: Create snapshot #1 with optimized settings for future comparison.

---

## Configuration Summary

### Before (Baseline - Current Production)
```python
# From report analysis
RSI_BUY_WEIGHT = 2.5
RSI_SELL_WEIGHT = 2.5
RSI_BUY_THRESHOLD = 25.0
RSI_SELL_THRESHOLD = 75.0
MIN_INDICATORS_REQUIRED = 0  # No requirement
EXCLUDED_SYMBOLS = []
MAX_SPREAD_PCT = None  # No filter

# Performance:
# - Win Rate: 13.3%
# - Profit Factor: 1.19
# - Expectancy: $0.02
# - Trades/day: 30
```

### After (Optimized - Testing)
```python
# Optimized settings
RSI_BUY_WEIGHT = 1.5  # ← Reduced from 2.5
RSI_SELL_WEIGHT = 1.5  # ← Reduced from 2.5
RSI_BUY_THRESHOLD = 20.0  # ← Tightened from 25.0
RSI_SELL_THRESHOLD = 80.0  # ← Tightened from 80.0
MIN_INDICATORS_REQUIRED = 2  # ← NEW: Multi-indicator confirmation
EXCLUDED_SYMBOLS = ["A8-USD", "PENGU-USD"]  # ← NEW: Blacklist losers
MAX_SPREAD_PCT = 0.50  # ← NEW: 0.5% max spread filter

# Expected Performance (7-day test):
# - Win Rate: 28-35%
# - Profit Factor: 1.8-2.2
# - Expectancy: $0.20-0.30
# - Trades/day: 12-15
```

---

## Deployment Plan

### Step 1: Local Testing (Optional)
```bash
# Run tests if available
pytest tests/

# Dry-run simulation (if available)
python -m scripts.backtest --config=optimized.json --days=7
```

### Step 2: Commit Changes
```bash
git add .env Config/config_manager.py sighook/signal_manager.py
git commit -m "feat: Optimize strategy settings for higher win rate

- Reduce RSI weight from 2.5 to 1.5 (reduce dominance)
- Add min_indicators_required=2 (multi-indicator confirmation)
- Blacklist A8-USD and PENGU-USD (consistent losers)
- Add max_spread filter at 0.5%

Expected improvements:
- Win rate: 13.3% → 28-35%
- Profit factor: 1.19 → 1.8-2.2
- Trade frequency: 30/day → 12-15/day

Tracked via strategy_snapshots for A/B comparison."
```

### Step 3: Deploy to AWS
```bash
# Pull latest code
ssh bottrader-aws "cd /opt/bot && git checkout main && git pull origin main"

# Update .env on AWS (if not committed)
ssh bottrader-aws "cd /opt/bot && nano .env"
# Update RSI_BUY_WEIGHT, RSI_SELL_WEIGHT, etc.

# Restart containers
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml restart webhook sighook"

# Verify startup
ssh bottrader-aws "docker logs webhook --tail 50"
ssh bottrader-aws "docker logs sighook --tail 50"
```

### Step 4: Monitor (7 Days)
```bash
# Daily checks
ssh bottrader-aws "docker logs webhook 2>&1 | grep -E 'insufficient_indicators|blacklist'"

# Check strategy snapshot created
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c 'SELECT * FROM current_strategy;'"

# Daily win rate
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT date, total_trades, win_rate, total_pnl_usd, profit_factor
FROM strategy_performance_summary
WHERE snapshot_id = (SELECT snapshot_id FROM current_strategy)
ORDER BY date DESC LIMIT 7;
\""
```

---

## Success Metrics

### Target Metrics (7-Day Average)
- ✅ Win Rate >= 25%
- ✅ Profit Factor >= 1.5
- ✅ Expectancy >= $0.15/trade
- ✅ Fast Exits (<60s) < 10%
- ✅ Total P&L > Baseline

### Comparison Query
```sql
SELECT
    ss.notes,
    sps.total_trades,
    sps.win_rate,
    sps.total_pnl_usd,
    sps.profit_factor,
    sps.expectancy_usd
FROM strategy_performance_summary sps
JOIN strategy_snapshots ss ON ss.snapshot_id = sps.snapshot_id
WHERE sps.date >= CURRENT_DATE - INTERVAL '7 days'
ORDER BY ss.active_from, sps.date;
```

---

## Rollback Plan

If metrics worse after 7 days:

```bash
# Option 1: Revert .env settings manually
ssh bottrader-aws "cd /opt/bot && nano .env"
# Change back to RSI_BUY_WEIGHT=2.5, remove MIN_INDICATORS_REQUIRED, etc.

# Option 2: Git revert
git revert <commit-hash>
git push origin feature/strategy-optimization

# Restart
ssh bottrader-aws "cd /opt/bot && git pull && docker compose -f docker-compose.aws.yml restart webhook sighook"

# Create rollback snapshot
python -c "
from sighook.strategy_snapshot_manager import StrategySnapshotManager
await snapshot_mgr.save_current_config(config, notes='Rollback to baseline - optimizations underperformed')
"
```

---

## Files Modified

### Configuration Files
- `.env` - Strategy settings
- `Config/config_manager.py` - Load new settings

### Code Changes
- `sighook/signal_manager.py` - Add multi-indicator confirmation logic

### Database
- `database/migrations/002_create_strategy_snapshots_table.sql` - Performance tracking tables

### Documentation
- `docs/STRATEGY_PERFORMANCE_TRACKING.md` - Usage guide
- `.claude/sessions/2025-12-08-strategy-optimization.md` - This file

---

## Next Session Handoff

### What to Check
1. **Daily email reports** - Look for:
   - Win rate trend (should increase)
   - Profit factor (should improve)
   - "insufficient_indicators" in trigger breakdown
   - No A8-USD or PENGU-USD trades

2. **Strategy comparison**:
```sql
SELECT * FROM strategy_comparison ORDER BY avg_win_rate DESC;
```

3. **Fast exits**:
```sql
SELECT fast_exits_count, fast_exits_pnl
FROM strategy_performance_summary
WHERE snapshot_id = (SELECT snapshot_id FROM current_strategy)
ORDER BY date DESC LIMIT 7;
```

### Decision Point (7 Days)
- If win rate > 25% → Keep settings, merge to main
- If 20-25% → Increase `min_indicators_required` to 3, test 7 more days
- If < 20% → Rollback to baseline

---

## Notes

### Assumptions
- Bot has 5 indicators per side (RSI, MACD, ROC, Touch, Ratio)
- Current score thresholds are around 3.0-4.0 (to be confirmed)
- Cooldown and hysteresis settings remain unchanged

### Risks
- Reducing trade frequency may reduce total P&L (but improve expectancy)
- Too strict multi-indicator requirement may miss good trades
- Blacklisting symbols may miss future opportunities

### Mitigation
- Run A/B test for only 7 days initially
- Use strategy_snapshots to track and compare
- Easy rollback via git revert

---

**Session Created**: 2025-12-08
**Created By**: Claude (Strategy Optimization Assistant)
**Status**: Ready for implementation
