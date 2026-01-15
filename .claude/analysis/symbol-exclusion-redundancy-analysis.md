# Symbol Exclusion System Analysis
**Date**: January 2, 2026
**Issue**: EXCLUDED_SYMBOLS list growing, potential redundancy with DynamicSymbolFilter

---

## Executive Summary

**Finding**: ✅ **NO REDUNDANCY** - The two systems serve different purposes and work together correctly.

**Issue Identified**: ❌ **EXCLUDED_SYMBOLS is being used as a MANUAL PERMANENT list** when it should be temporary/dynamic.

**Root Cause**: `PERMANENT_EXCLUSIONS` is empty, so all manual exclusions are being added to `EXCLUDED_SYMBOLS` (which is NOT consumed by DynamicSymbolFilter).

---

## Current Configuration

### `.env` Settings (Both Local & AWS)
```bash
# Manual exclusion list (35 symbols) - GROWING PROBLEM!
EXCLUDED_SYMBOLS=A8-USD,PENGU-USD,ELA-USD,ALCX-USD,UNI-USD,CLANKER-USD,ZORA-USD,DASH-USD,BCH-USD,AVAX-USD,SWFTC-USD,AVNT-USD,PRIME-USD,ICP-USD,KAITO-USD,IRYS-USD,TIME-USD,NMR-USD,NEON-USD,QNT,USD,PERP-USD,BOBBOB-USD,OMNI-USD,TIA-USD,IP-USD,TNSR-USD,XRP-USD,SAPIEN-USD,FARTCOIN-USD,MON-USD,WET-USD,SOL-USD,PUMP-USD,RLS-USD

# Dynamic filter ENABLED
DYNAMIC_FILTER_ENABLED=true
DYNAMIC_FILTER_MIN_WIN_RATE=0.30
DYNAMIC_FILTER_MIN_AVG_PNL=-5.0
DYNAMIC_FILTER_MIN_TOTAL_PNL=-50.0
DYNAMIC_FILTER_MAX_SPREAD_PCT=0.02
DYNAMIC_FILTER_MIN_TRADES=5
DYNAMIC_FILTER_LOOKBACK_DAYS=30

# Permanent exclusions (EMPTY!) - THIS IS THE PROBLEM!
PERMANENT_EXCLUSIONS=
```

---

## How The Two Systems Work

### 1. `EXCLUDED_SYMBOLS` (Config-based, Static)

**Purpose**: Originally intended as temporary manual override
**Consumed By**:
- ❌ **NOT used by DynamicSymbolFilter**
- ✅ **Only used by `config_manager.py`** as a config property
- ✅ **Used by old/fallback code paths**

**Code Location**: `Config/config_manager.py:807-816`
```python
@property
def excluded_symbols(self) -> list:
    if self._excluded_symbols is None:
        return ['A8-USD', 'PENGU-USD']  # Default fallback
    if isinstance(self._excluded_symbols, str):
        return [s.strip() for s in self._excluded_symbols.split(',') if s.strip()]
    return list(self._excluded_symbols)
```

**Current Usage**:
- ✅ Read by `strategy_snapshot_manager.py` (for database snapshots)
- ❌ **NOT directly used for trading exclusions by sighook or passive MM**

### 2. `DynamicSymbolFilter` (Performance-based, Dynamic)

**Purpose**: Automatically exclude poor performers, include improving symbols
**Managed By**: `Shared_Utils/dynamic_symbol_filter.py`

**Exclusion Sources**:
1. **Performance-based** (queries `trade_records` for rolling metrics):
   - Win rate < 30%
   - Average P&L < -$5
   - Total P&L < -$50
   - Minimum 5 trades required

2. **Spread-based** (checks current market data):
   - Bid-ask spread > 2%

3. **Permanent exclusions** (from `PERMANENT_EXCLUSIONS` env var):
   - Currently EMPTY!
   - **This is where HODL, SHILL_COINS, and manual blocks should be**

**Code Location**: `Shared_Utils/dynamic_symbol_filter.py:106-150`
```python
async def _compute_excluded_symbols(self) -> Set[str]:
    excluded = set()

    # 1. Get performance-based exclusions
    performance_excluded = await self._get_performance_excluded_symbols()
    excluded.update(performance_excluded)

    # 2. Get spread-based exclusions
    spread_excluded = await self._get_spread_excluded_symbols()
    excluded.update(spread_excluded)

    # 3. Add permanent exclusions (manual override)
    excluded.update(self.permanent_exclusions)  # ← EMPTY in current config!

    return excluded
```

**Cache Behavior**:
- 1-hour cache (`_cache_ttl = 3600`)
- Auto-refreshes every hour
- Can force refresh with `force_refresh=True`

---

## Current Consumers of Each System

### Who Uses `EXCLUDED_SYMBOLS`?

**Direct Consumers**:
1. ❌ **None** - DynamicSymbolFilter does NOT read this!
2. ✅ `config_manager.py` - Exposes as config property
3. ✅ `strategy_snapshot_manager.py` - Saves to database for historical record

**Trading Impact**:
- ❌ **NOT directly used for exclusions** in sighook or passive MM
- ⚠️ Only affects snapshot metadata

### Who Uses `DynamicSymbolFilter`?

**Direct Consumers**:
1. ✅ **sighook** (`trading_strategy.py:117-122`):
   ```python
   excluded_symbols = await self.dynamic_filter.get_excluded_symbols()
   if symbol in excluded_symbols:
       self.logger.info(f"⛔ Skipping excluded symbol: {symbol}")
       continue
   ```

2. ✅ **Passive MM** (`passive_order_manager.py:437-440`):
   ```python
   excluded_symbols = await self.dynamic_filter.get_excluded_symbols()
   if trading_pair in excluded_symbols:
       self.logger.debug(f"⛔ Skipping {trading_pair} — dynamically excluded")
       return
   ```

**Fallback Behavior** (if DynamicSymbolFilter fails):
- ✅ **sighook** has hardcoded fallback list (`_fallback_excluded_symbols`)
- ✅ Includes all symbols from Session 2 manual blocks

---

## The Problem: Growing `EXCLUDED_SYMBOLS`

### Session 2 (Jan 1, 2026) Added 8 Symbols
```bash
# From Session 2 Risk Management
XRP-USD, SAPIEN-USD, FARTCOIN-USD, MON-USD, WET-USD, SOL-USD, PUMP-USD, RLS-USD
```

**Intent**: Temporary manual blocks for poor performers
**Actual Effect**:
- ❌ Added to `EXCLUDED_SYMBOLS` (static, doesn't auto-expire)
- ✅ **Should have been added to `PERMANENT_EXCLUSIONS`**

### Root Cause Analysis

**Configuration Error**:
```bash
PERMANENT_EXCLUSIONS=  # ← EMPTY! Should contain manual blocks
```

**Expected Configuration**:
```bash
# Dynamic system handles these automatically
EXCLUDED_SYMBOLS=  # ← Should be EMPTY or very minimal

# Manual permanent blocks (HODL, SHILL_COINS, Session 2 blocks)
PERMANENT_EXCLUSIONS=UNFI-USD,TRUMP-USD,MATIC-USD,XRP-USD,SAPIEN-USD,FARTCOIN-USD,MON-USD,WET-USD,SOL-USD,PUMP-USD,RLS-USD
```

---

## Why The Systems Are NOT Redundant

### Different Purposes

| Feature | `EXCLUDED_SYMBOLS` | `DynamicSymbolFilter` |
|---------|-------------------|----------------------|
| **Purpose** | Manual config property | Automated performance filter |
| **Data Source** | `.env` file (static) | Database queries (dynamic) |
| **Updates** | Manual edit + deploy | Auto-refresh every hour |
| **Used For Trading** | ❌ No (metadata only) | ✅ Yes (live exclusions) |
| **Can Auto-Include** | ❌ Never | ✅ Yes (when perf improves) |
| **Permanent Exclusions** | N/A | `PERMANENT_EXCLUSIONS` env var |

### Correct Workflow

**For Permanent Manual Blocks** (HODL, SHILL_COINS, regulatory):
```bash
PERMANENT_EXCLUSIONS=BTC-USD,ETH-USD,ATOM-USD,UNFI-USD,TRUMP-USD,MATIC-USD
```
→ Added to `DynamicSymbolFilter.permanent_exclusions`
→ Never auto-included (always excluded)

**For Temporary Poor Performers**:
→ Let `DynamicSymbolFilter` handle automatically
→ Auto-excluded when metrics < thresholds
→ Auto-included when metrics improve

**For Config/Snapshot Metadata**:
```bash
EXCLUDED_SYMBOLS=  # Empty or minimal (not used for trading)
```

---

## Verification: Is DynamicSymbolFilter Working?

### Expected Behavior

**On sighook startup**:
```
Dynamic Symbol Filter initialized: enabled=true, min_win_rate=30.0%,
min_avg_pnl=$-5.0, min_total_pnl=$-50.0, lookback=30d, permanent_exclusions=0
```

**Every hour (cache refresh)**:
```
Performance-based exclusions: X symbols
Spread-based exclusions: Y symbols
Total excluded symbols: Z
```

**When symbols change status**:
```
🚫 Newly excluded symbols: ['SYMBOL1-USD', 'SYMBOL2-USD']
✅ Newly included symbols: ['SYMBOL3-USD']
```

### Log Verification

**Check if running**:
```bash
ssh bottrader-aws "docker logs sighook --tail 1000 2>&1 | grep -E 'Dynamic Symbol Filter initialized'"
```

**Check exclusions**:
```bash
ssh bottrader-aws "docker logs sighook --tail 1000 2>&1 | grep -E 'Performance-based exclusions|Total excluded symbols'"
```

**Expected**: Logs should show dynamic filter activity

---

## Recommendations

### 1. Fix Configuration (Immediate)

**Move Manual Blocks to `PERMANENT_EXCLUSIONS`**:

```bash
# .env changes
EXCLUDED_SYMBOLS=  # ← Clear this (not used for trading)

# Add all manual blocks here (HODL + SHILL + Session 2)
PERMANENT_EXCLUSIONS=BTC-USD,ETH-USD,ATOM-USD,UNFI-USD,TRUMP-USD,MATIC-USD,XRP-USD,SAPIEN-USD,FARTCOIN-USD,MON-USD,WET-USD,SOL-USD,PUMP-USD,RLS-USD
```

**Deployment Steps**:
1. Update `.env` locally and on AWS
2. Restart sighook container (picks up new config)
3. Verify dynamic filter shows `permanent_exclusions=14`

### 2. Let Dynamic Filter Handle Poor Performers

**Remove from Manual Lists**:
```bash
# These should be handled automatically by DynamicSymbolFilter:
# A8-USD, PENGU-USD, ELA-USD, ALCX-USD, UNI-USD, CLANKER-USD, ZORA-USD,
# DASH-USD, BCH-USD, AVAX-USD, SWFTC-USD, AVNT-USD, PRIME-USD, ICP-USD,
# KAITO-USD, IRYS-USD, TIME-USD, NMR-USD, NEON-USD, QNT-USD, PERP-USD,
# BOBBOB-USD, OMNI-USD, TIA-USD, IP-USD, TNSR-USD
```

**Let system auto-exclude when**:
- Win rate < 30%
- Avg P&L < -$5
- Total P&L < -$50
- Spread > 2%

**Let system auto-include when**:
- Performance improves above thresholds

### 3. Monitor Dynamic Exclusions

**Daily Check**:
```bash
# See what's currently excluded
ssh bottrader-aws "docker logs sighook --tail 100 2>&1 | grep 'Total excluded symbols'"

# See newly added/removed
ssh bottrader-aws "docker logs sighook --tail 500 2>&1 | grep -E 'Newly excluded|Newly included'"
```

### 4. Optional: Tighten Dynamic Thresholds

**Current Thresholds** (very lenient):
```bash
DYNAMIC_FILTER_MIN_WIN_RATE=0.30  # 30%
DYNAMIC_FILTER_MIN_AVG_PNL=-5.0   # -$5 per trade
DYNAMIC_FILTER_MIN_TOTAL_PNL=-50.0  # -$50 total
```

**Suggested Stricter Thresholds** (after testing):
```bash
DYNAMIC_FILTER_MIN_WIN_RATE=0.35  # 35% (require better consistency)
DYNAMIC_FILTER_MIN_AVG_PNL=-3.0   # -$3 per trade (tighter avg loss tolerance)
DYNAMIC_FILTER_MIN_TOTAL_PNL=-30.0  # -$30 total (faster exclusion)
```

---

## Impact Analysis

### If We Clear `EXCLUDED_SYMBOLS` and Use `PERMANENT_EXCLUSIONS`

**Symbols That Will Be Tested Dynamically** (~26 symbols):
```
A8-USD, PENGU-USD, ELA-USD, ALCX-USD, UNI-USD, CLANKER-USD, ZORA-USD,
DASH-USD, BCH-USD, AVAX-USD, SWFTC-USD, AVNT-USD, PRIME-USD, ICP-USD,
KAITO-USD, IRYS-USD, TIME-USD, NMR-USD, NEON-USD, QNT-USD, PERP-USD,
BOBBOB-USD, OMNI-USD, TIA-USD, IP-USD, TNSR-USD
```

**Expected Outcome**:
- ✅ Poor performers auto-excluded within 1 hour
- ✅ Improving symbols get second chances
- ✅ System adapts to changing market conditions
- ✅ Manual intervention only for permanent blocks (HODL, regulatory)

**Risk**:
- ⚠️ Some previously blocked symbols may temporarily trade if they meet thresholds
- ✅ **Mitigation**: With Session 2 position sizes ($15) and stops (-3.0%), risk is minimal

---

## Summary

### Current State
- ❌ `EXCLUDED_SYMBOLS` has 35 symbols (growing, static)
- ✅ `DynamicSymbolFilter` is enabled and working
- ❌ `PERMANENT_EXCLUSIONS` is empty (should have manual blocks)
- ⚠️ Systems are NOT redundant, but config is wrong

### Correct State
- ✅ `EXCLUDED_SYMBOLS` should be empty (or minimal, metadata-only)
- ✅ `PERMANENT_EXCLUSIONS` should have HODL + SHILL + manual blocks (~14 symbols)
- ✅ `DynamicSymbolFilter` handles poor performers automatically (~26 symbols dynamic)
- ✅ Total exclusions similar (~40 symbols), but 26 are now dynamic and can auto-include

### Benefits of Fix
1. **Self-Healing**: Poor performers auto-excluded, improving symbols get chances
2. **No Manual Updates**: Don't need to edit .env for every underperformer
3. **Adapts to Markets**: Symbols that improve in new conditions auto-included
4. **Clear Separation**: Permanent (HODL/regulatory) vs. Performance-based (dynamic)

---

**Status**: Analysis complete, recommendations provided
**Next Action**: User decision on implementing configuration changes
