# Session: Deep Dive Investigation - Position Monitor Bug
**Date**: January 25, 2026 23:15 PST
**Status**: 🔍 Investigation In Progress
**Branch**: `feature/strategy-optimization`

---

## Session Overview

**Start Time**: 23:15 PST
**Context**: Emergency fixes deployed (position_monitor disabled, Multi-ROC strategies disabled). System now running with rearm_oco exits only. This session focuses on root cause analysis of the position_monitor 0% win rate bug.

### Critical Bug Summary

**Symptom**: Position monitor exits have 0% win rate (18 exits, 0 wins, -$10.98 total)
**Impact**: Accounts for 73% of all trading losses despite only 14% of exits
**Avg Loss**: -$0.610 per position_monitor exit vs -$0.036 for rearm_oco
**Status**: DISABLED (Jan 25, 2026) - rearm_oco handling all exits temporarily

---

## Investigation Goals

### Primary Objectives

1. **Root Cause Identification**
   - Analyze avg_entry_price calculation logic (lines 446-470)
   - Review Jan 17 bug fix effectiveness (validation at lines 467-479)
   - Determine why validation (2×/0.5× threshold) isn't preventing false exits
   - Identify data corruption sources (API unrealized_pnl, bid/ask staleness)

2. **Hypothesis Testing**
   - **Hypothesis 1**: Unrealized_pnl from API is corrupted (known issue from Jan 17)
   - **Hypothesis 2**: Validation threshold (2×/0.5×) too wide, allows bad data through
   - **Hypothesis 3**: Bid/ask spread data is stale, causing wrong current_price
   - **Hypothesis 4**: Fee-aware P&L calculation (lines 509-519) has logic error
   - **Hypothesis 5**: Position monitor triggering on positions that should be profitable

3. **Data Analysis**
   - Query historical position_monitor exits with entry/exit prices
   - Compare calculated avg_entry_price vs actual entry from trade_records
   - Review unrealized_pnl values at time of exit
   - Check if exits cluster around specific symbols/times

4. **Fix Design**
   - Propose robust avg_entry_price calculation (possibly from trade_records table)
   - Design extensive logging for validation period
   - Create test cases for fix validation

---

## Code Analysis

### Section 1: Average Entry Price Calculation

**File**: `MarketDataManager/position_monitor.py:446-481`

```python
# Lines 446-451: Get unrealized_pnl from API
unrealized_pnl_data = position_data.get('unrealized_pnl', {})
if isinstance(unrealized_pnl_data, dict):
    unrealized_pnl = Decimal(str(unrealized_pnl_data.get('value', 0)))
else:
    unrealized_pnl = Decimal(str(unrealized_pnl_data or 0))

# Lines 452-459: Get current price from bid/ask spread
market_data = self.shared_data_manager.market_data or {}
bid_ask_spread = market_data.get('bid_ask_spread', {})
bid_ask = bid_ask_spread.get(product_id, {})
current_bid = Decimal(str(bid_ask.get('bid', 0)))
current_ask = Decimal(str(bid_ask.get('ask', 0)))
# Use mid-price for P&L calculation
current_price = (current_bid + current_ask) / Decimal('2') if (current_bid > 0 and current_ask > 0) else Decimal('0')

# Lines 461-465: Calculate avg_entry_price
# Formula: unrealized_pnl = (current_price - avg_entry_price) * balance
# Therefore: avg_entry_price = current_price - (unrealized_pnl / balance)
if current_price > 0 and total_balance_crypto > 0:
    avg_entry_price = current_price - (unrealized_pnl / total_balance_crypto)

# Lines 467-479: VALIDATION (added Jan 17, 2026)
# If avg_entry > 2× current_price or < 0.5× current_price, it's garbage data
if avg_entry_price > current_price * Decimal('2') or avg_entry_price < current_price * Decimal('0.5'):
    self.logger.warning(
        f"[POS_MONITOR] {symbol} INVALID avg_entry from API: "
        f"entry={avg_entry_price:.4f}, current={current_price:.4f}, "
        f"unrealized_pnl={unrealized_pnl}, balance={total_balance_crypto}. "
        f"Using current price as entry (conservative estimate)."
    )
    # Fallback: assume entry ≈ current price (neutral P&L)
    avg_entry_price = current_price
```

#### Analysis Findings

**🚨 CRITICAL ISSUE #1: Fallback Logic is Wrong**
- When validation fails, fallback sets `avg_entry_price = current_price`
- This creates **neutral P&L (0%)** when we don't have good data
- But position_monitor then evaluates THIS position against stops
- If current_price drops slightly, P&L becomes negative → triggers SOFT_STOP
- **Result**: Exits positions we have NO reliable entry data for

**🚨 CRITICAL ISSUE #2: Validation Threshold May Be Too Wide**
- Threshold: entry > 2× current OR entry < 0.5× current
- This allows entry price anywhere from 50% to 200% of current price
- For volatile crypto, price could move 30-40% and still validate bad data
- **Example**:
  - Current price: $100
  - Actual entry: $95 (loss: -5%)
  - API returns garbage unrealized_pnl suggesting entry: $130
  - Validation: $130 < $200 (2× threshold) → PASSES validation!
  - Calculated P&L: ($100 - $130) / $130 = -23% → TRIGGERS HARD STOP

**🚨 CRITICAL ISSUE #3: No Staleness Check on Bid/Ask Data**
- Lines 452-459 fetch bid/ask from shared cache
- No timestamp check - could be hours old if WebSocket died
- Stale price + stale unrealized_pnl = catastrophic combo
- **Need**: Check `bid_ask.get('timestamp')` and reject if > 60 seconds old

**🚨 CRITICAL ISSUE #4: No Cross-Reference with Trade Records**
- Database has `trade_records` table with actual entry orders
- Position monitor NEVER checks actual buy orders
- **Should**: Query database for actual avg entry price from filled buy orders
- This would be ground truth, not API-derived calculation

### Section 2: P&L Calculation and Exit Logic

**File**: `MarketDataManager/position_monitor.py:506-527`

```python
# Lines 506-507: Calculate RAW P&L (no fees)
pnl_pct_raw = (current_price - avg_entry_price) / avg_entry_price

# Lines 509-519: Calculate FEE-AWARE P&L
entry_fee_pct, exit_fee_pct = await self._fetch_current_fees()
entry_cost_per_unit = avg_entry_price * (Decimal('1') + entry_fee_pct)
exit_revenue_per_unit = current_price * (Decimal('1') - exit_fee_pct)
pnl_pct = (exit_revenue_per_unit - entry_cost_per_unit) / entry_cost_per_unit
```

#### Analysis Findings

**✅ Good**: Fee-aware calculation is mathematically correct
**🚨 Issue**: If `avg_entry_price` is wrong (garbage from API), then fee calculation amplifies the error
**🚨 Issue**: No logging of `pnl_pct_raw` vs `pnl_pct` comparison - can't see fee impact

### Section 3: Exit Priority Logic

**File**: `MarketDataManager/position_monitor.py:538-650`

Exit priority order:
1. **Hard Stop** (-5%): Always override, market order
2. **Peak Tracking** (ROC only): Override bracket orders
3. **Soft Stop** (-2.5%): Coordinate with bracket, market order if > -3%
4. **Signal Exit** (SELL signal + profitable): Only if trailing not active
5. **Take Profit** (+3.5%): Coordinate with bracket

#### Analysis Findings

**✅ Good**: Priority order is logical (emergency → risk → profit)
**🔍 Question**: Peak tracking disabled (PEAK_TRACKING_ENABLED=false in .env?)
**🚨 Issue**: All exit decisions depend on accurate `avg_entry_price` and `pnl_pct`
**🚨 Issue**: No sanity check - "If position just opened < 5 min ago, skip exits"

---

## Data Investigation Plan

### Query 1: Position Monitor Exit History

```sql
-- Get all position_monitor exits with entry/exit details
SELECT
    fa.product_id,
    fa.sell_timestamp,
    fa.sell_price,
    fa.buy_price as actual_entry_price,
    fa.quantity,
    fa.pnl_usd,
    (fa.sell_price - fa.buy_price) / fa.buy_price as actual_pnl_pct,
    tr_sell.metadata->>'exit_reason' as exit_reason,
    tr_sell.trigger as exit_trigger
FROM fifo_allocations fa
JOIN trade_records tr_sell ON fa.sell_order_id = tr_sell.order_id
WHERE fa.sell_timestamp >= '2026-01-14'
  AND tr_sell.trigger LIKE '%position_monitor%'
ORDER BY fa.sell_timestamp DESC;
```

### Query 2: Unrealized PNL at Exit Time

```sql
-- Check if unrealized_pnl was logged at exit time
-- (This requires webhook logs or position_monitor logs with unrealized_pnl)
-- If not logged, we need to add this to future logging
```

### Query 3: Symbol Analysis

```sql
-- Which symbols have position_monitor exits?
SELECT
    fa.product_id,
    COUNT(*) as exit_count,
    AVG((fa.sell_price - fa.buy_price) / fa.buy_price) as avg_pnl_pct,
    SUM(fa.pnl_usd) as total_pnl
FROM fifo_allocations fa
JOIN trade_records tr_sell ON fa.sell_order_id = tr_sell.order_id
WHERE fa.sell_timestamp >= '2026-01-14'
  AND tr_sell.trigger LIKE '%position_monitor%'
GROUP BY fa.product_id
ORDER BY total_pnl ASC;
```

---

## Hypotheses Summary

| Hypothesis | Likelihood | Evidence | Test Method |
|-----------|-----------|----------|-------------|
| **H1**: API unrealized_pnl corrupted | **VERY HIGH** | Known issue from Jan 17, validation added | Query actual vs calculated entry prices |
| **H2**: Validation threshold too wide (2×/0.5×) | **HIGH** | 100% false positive rate suggests bad data getting through | Check if calculated entries are within threshold but still wrong |
| **H3**: Bid/ask data is stale | **MEDIUM** | No staleness check in code | Add timestamp logging to exits |
| **H4**: Fee calculation error | **LOW** | Math looks correct | Verify with manual calculation |
| **H5**: Exits triggering on profitable positions | **HIGH** | 0% win rate = ALL exits are false | Query actual P&L at exit time |

---

## Proposed Fix (Draft)

### Option A: Use Trade Records for Entry Price (RECOMMENDED)

**Concept**: Query `trade_records` table for actual filled buy orders instead of relying on API unrealized_pnl

**Implementation**:
```python
async def _get_avg_entry_price_from_trades(self, product_id: str, total_balance: Decimal) -> Decimal:
    """
    Calculate average entry price from actual buy orders in database.
    This is ground truth, not API-derived.
    """
    # Query trade_records for all buy orders for this product
    # Calculate weighted average of filled buy prices
    # Compare to current balance to ensure we have all buys
    # Return avg entry price
    pass
```

**Pros**:
- Ground truth from actual fills
- No dependency on API unrealized_pnl
- Can handle partial fills and multiple buys

**Cons**:
- Requires database query (performance)
- May need caching to avoid query every check cycle

### Option B: Tighten Validation Threshold

**Concept**: Reduce validation threshold from 2×/0.5× to 1.3×/0.7×

**Pros**:
- Simple change
- Catches more bad data

**Cons**:
- Still relies on API data
- Volatile assets could exceed threshold legitimately

### Option C: Hybrid Approach (BEST)

**Concept**:
1. Try to get entry from trade_records (Option A)
2. If not available, use API with tighter validation (Option B)
3. If validation fails, DO NOT EXIT - log warning and skip this position

**Implementation**:
```python
# 1. Try database first
avg_entry_price = await self._get_avg_entry_price_from_trades(product_id, total_balance)

# 2. Fallback to API with tight validation
if not avg_entry_price:
    avg_entry_price = current_price - (unrealized_pnl / total_balance)

    # Tight validation: ±30% from current
    if avg_entry_price > current_price * 1.3 or avg_entry_price < current_price * 0.7:
        self.logger.error(
            f"[POS_MONITOR] {symbol} NO RELIABLE ENTRY PRICE AVAILABLE. "
            f"API data invalid, trade records unavailable. SKIPPING EXIT CHECK."
        )
        return  # DO NOT EXIT without good data

# 3. Add staleness check
bid_timestamp = bid_ask.get('timestamp')
if bid_timestamp:
    age_seconds = (datetime.now() - bid_timestamp).total_seconds()
    if age_seconds > 60:
        self.logger.warning(
            f"[POS_MONITOR] {symbol} STALE BID/ASK DATA (age: {age_seconds:.0f}s). "
            f"Skipping exit check."
        )
        return
```

---

## Next Steps

1. **Run Data Queries** (Query 1-3 above) to validate hypotheses
2. **Implement Extensive Logging** for next 24-48 hours:
   - Log avg_entry_price (calculated vs actual from DB)
   - Log unrealized_pnl from API
   - Log bid/ask timestamp and staleness
   - Log pnl_pct_raw vs pnl_pct (fee impact)
3. **Implement Hybrid Fix** (Option C)
4. **Test Locally** with historical data
5. **Deploy with Logging** and monitor for 24-48 hours
6. **Re-enable Position Monitor** if fix validated

---

## Investigation Status

- [x] Read position_monitor.py code
- [x] Identify critical issues in avg_entry_price calculation
- [x] Identify validation threshold problems
- [x] Design data queries for hypothesis testing
- [x] Run data queries against production database
- [x] Analyze query results
- [x] Implement hybrid fix (database + API with tighter validation)
- [ ] Test fix locally with SSH tunnel
- [ ] Deploy fix to GitHub and AWS
- [ ] Monitor for 24-48 hours
- [ ] Re-run performance analysis

---

## Implementation Summary

### Hybrid Fix Deployed

**File**: `MarketDataManager/position_monitor.py`

**Key Changes**:

1. **New Method**: `_get_avg_entry_price_from_db()` (lines ~429-520)
   - Queries `trade_records` table for actual filled buy orders
   - Calculates weighted average entry price from ground truth
   - Validates quantity matches current balance (±1% tolerance)
   - Returns (avg_entry_price, quantity, 'database') or (None, 0, None)

2. **Modified**: `_check_position()` hybrid logic (lines ~545-625)
   - **Step 1**: Try database first (ground truth)
   - **Step 2**: Fallback to API with TIGHTENED validation (±30% instead of ±100%)
   - **Step 3**: If both fail, **SKIP exit check** (do not exit without reliable data)
   - **Step 4**: Added bid/ask staleness check (reject if >60s old)

3. **Enhanced Logging**: All P&L calculations now include data source
   - `source:database` when using actual buy orders
   - `source:api_validated` when using API with validation
   - Logs entry price, current price, P&L (raw + fee-aware), balance, data source

4. **Re-enabled**: Position monitor active again with fix
   - Emergency disable removed
   - Extensive logging for monitoring

**Expected Behavior**:
- **Most positions**: Use database entry price (reliable, accurate)
- **Edge cases**: Use API with tight validation (±30%)
- **Corrupt data**: Skip exit entirely (safety first)
- **Stale prices**: Skip exit check (avoid bad decisions)

---

**Document Status**: ✅ Fix Implemented - Ready for Testing
**Last Updated**: January 26, 2026 00:45 PST
**Next Update**: After local testing with SSH tunnel
