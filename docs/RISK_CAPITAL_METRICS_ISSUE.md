# Risk & Capital Metrics Issue Analysis

## Summary
The "Risk & Capital" section in email reports shows incorrect/unrealistic values:
- **Max Drawdown**: 99,690.5% ❌ (should be reasonable, e.g., 10-50%)
- **Cash (USD)**: $0.00 ❌ (missing table)
- **Invested Notional (USD)**: $65.33 ✅ (correct)
- **Invested %**: 0.0% ❌ (calculation fails due to missing cash)

## Root Causes

### Issue 1: Max Drawdown (99,690.5%)

**Location**: `botreport/aws_daily_report.py:1204-1289` - `compute_max_drawdown()`

**Problem**:
The drawdown calculation builds an equity curve from **cumulative PnL only**, without a starting capital balance. This causes unrealistic percentage calculations when the equity curve starts negative or near-zero.

**Database Evidence**:
```sql
Peak Equity:   $1.28       (tiny positive from brief profitable trade)
Trough Equity: -$1,271.67  (cumulative loss)
Drawdown:      (-1271.67 - 1.28) / 1.28 = -99,690%
```

**The Calculation**:
```sql
-- Current implementation (lines 1230-1276)
WITH trade_pnl AS (
  SELECT tr.order_time AS ts, COALESCE(SUM(fa.pnl_usd), 0) AS pnl
  FROM public.trade_records tr
  LEFT JOIN fifo_allocations fa ON fa.sell_order_id = tr.order_id
  WHERE tr.side = 'sell' AND tr.status IN ('filled', 'done')
  GROUP BY tr.order_time, tr.order_id
),
c AS (
  SELECT ts, SUM(pnl) OVER (ORDER BY ts) AS equity  -- ⚠️ Starts at 0, not starting capital
  FROM trade_pnl
)
```

**Anchor Logic (Lines 1224-1263)**:
```python
min_start_equity = float(os.getenv("REPORT_MDD_MIN_START_EQUITY", "500"))
```

The code tries to anchor drawdown calculation at the first point where `equity >= $500`, but:
- Current equity **never reaches $500** (max is $1.28)
- When anchor is NULL, it uses **all data** from the beginning
- This causes the tiny $1.28 peak to become the denominator

**Why This Is Wrong**:
- Trading accounts START with capital (e.g., $3,000)
- Equity should be: `starting_capital + cumulative_pnl`
- Current: `0 + cumulative_pnl` (no starting capital)
- Drawdown from $3,000 to $1,728 = **42.4%** (realistic)
- Drawdown from $1.28 to -$1,271 = **99,690%** (nonsense)

---

### Issue 2: Cash (USD) - $0.00

**Location**: `botreport/aws_daily_report.py:1292-1321` - `compute_cash_vs_invested()`

**Problem**: The function queries `public.report_balances` table which **does not exist**.

**Configuration**: `Config/constants_report.py:42`
```python
REPORT_BALANCES_TABLE = os.getenv('REPORT_BALANCES_TABLE', 'public.report_balances')
```

**Code**:
```python
tbl = REPORT_BALANCES_TABLE  # 'public.report_balances'
cols = table_columns(conn, tbl)
if not cols:
    notes.append(f"Cash: table not found: {tbl}")
    return 0.0, invested, 0.0, notes  # ⚠️ Returns cash=0, invested_pct=0
```

**Database Verification**:
```sql
SELECT table_name FROM information_schema.tables
WHERE table_name LIKE '%balance%';
-- Result: 0 rows (table doesn't exist)
```

**Expected Behavior**:
The function should query actual account balances from Coinbase API or a balance tracking table to get USD/USDC/USDT holdings.

**Notes from Report**:
```
Cash: table not found: public.report_balances
```

---

### Issue 3: Invested % - 0.0%

**Location**: `botreport/aws_daily_report.py:1318-1319`

**Problem**: Calculation depends on cash value, which is $0 due to missing table.

**Code**:
```python
cash = float(row or 0.0)  # Returns 0.0 (table missing)
total_cap = cash + invested  # = 0 + 65.33 = 65.33
invested_pct = (invested / total_cap * 100.0) if total_cap > 0 else 0.0
# Should be: (65.33 / 65.33 * 100) = 100.0%
# But function returns early with 0.0 when table not found (line 1299)
```

**Actual Return** (Line 1299):
```python
if not cols:
    notes.append(f"Cash: table not found: {tbl}")
    return 0.0, invested, 0.0, notes  # ← invested_pct hardcoded to 0.0
```

---

## Fix Options

### Option 1: Fix Max Drawdown - Add Starting Equity (Recommended)

Modify `compute_max_drawdown()` to include starting capital in equity curve.

**Change**: `botreport/aws_daily_report.py:1245-1252`

**From**:
```sql
c AS (  -- equity curve
  SELECT ts,
         SUM(pnl) OVER (ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS equity
  FROM t
),
```

**To**:
```sql
c AS (  -- equity curve with starting capital
  SELECT ts,
         {STARTING_EQUITY} + SUM(pnl) OVER (ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS equity
  FROM t
),
```

Where `STARTING_EQUITY` = `float(os.getenv("STARTING_EQUITY_USD", "3000"))`

**Impact**:
- Realistic drawdown percentages (e.g., 10-50%)
- Equity curve starts at actual account capital
- Anchor logic ($500 threshold) would work correctly
- No breaking changes to existing logic

**Example With Fix**:
```
Starting Equity: $3,000
Peak Equity: $3,001.28  (starting + $1.28 profit)
Trough Equity: $1,728.33  (starting - $1,271.67 loss)
Drawdown: (1728.33 - 3001.28) / 3001.28 = -42.4% ✅
```

---

### Option 2: Fix Cash - Create or Query Balance Table

**Approach A: Create report_balances table**

Create a table to track cash balances, populated by periodic balance snapshots or calculated from trade_records.

```sql
CREATE TABLE IF NOT EXISTS public.report_balances (
    symbol VARCHAR(10),
    balance NUMERIC(20,8),
    available NUMERIC(20,8),
    last_updated TIMESTAMPTZ DEFAULT NOW()
);
```

**Approach B: Calculate from Starting Equity minus Invested**

Simpler approach - calculate cash as:
```python
starting_equity = float(os.getenv("STARTING_EQUITY_USD", "3000"))
cash = starting_equity - invested
```

This assumes all capital is either cash or invested (no unrealized PnL in calculation).

**Approach C: Query Coinbase API**

Fetch real-time balance from Coinbase (may add latency to report generation).

---

### Option 3: Fix Invested % - Handle Missing Table Gracefully

**Change**: `botreport/aws_daily_report.py:1297-1299`

**From**:
```python
if not cols:
    notes.append(f"Cash: table not found: {tbl}")
    return 0.0, invested, 0.0, notes  # ← Hardcoded 0.0%
```

**To** (if using Approach B above):
```python
if not cols:
    notes.append(f"Cash: table not found: {tbl}, using calculated from starting equity")
    starting_equity = float(os.getenv("STARTING_EQUITY_USD", "3000"))
    cash = max(0.0, starting_equity - invested)  # Can't be negative
    total_cap = cash + invested
    invested_pct = (invested / total_cap * 100.0) if total_cap > 0 else 0.0
    return cash, invested, invested_pct, notes
```

---

## Recommended Implementation Plan

### Phase 1: Max Drawdown Fix (High Impact, Low Risk)
1. Modify `compute_max_drawdown()` to add `STARTING_EQUITY_USD` to equity curve
2. Test with current data to verify realistic percentages
3. Deploy and verify in next report

### Phase 2: Cash Calculation Fix (Medium Impact, Low Risk)
1. Use "Approach B" - calculate cash from starting equity minus invested
2. Update `compute_cash_vs_invested()` to handle missing table gracefully
3. Add `STARTING_EQUITY_USD` to .env if not already present
4. Deploy and verify Invested % shows realistic values

### Phase 3: Balance Table (Future Enhancement)
1. Create `report_balances` table schema
2. Implement periodic balance snapshot from Coinbase API
3. Migrate cash calculation to use table when available
4. Keeps fallback to calculated method for resilience

---

## Environment Variables

Add to `.env` if not present:
```bash
# Starting equity for drawdown and cash calculations
STARTING_EQUITY_USD=3000

# Max drawdown anchor threshold (already present)
REPORT_MDD_MIN_START_EQUITY=500

# Balance table (optional, for future use)
REPORT_BALANCES_TABLE=public.report_balances
```

---

## Expected Results After Fix

**Before**:
```
Max Drawdown (since inception)    Cash (USD)    Invested Notional (USD)    Invested %
99690.5%                          $0.00         $65.33                     0.0%
```

**After** (assuming $3,000 starting equity, -$1,271 total PnL):
```
Max Drawdown (since inception)    Cash (USD)    Invested Notional (USD)    Invested %
42.4%                             $1,663.34     $65.33                     3.8%
```

Calculation:
- Starting Equity: $3,000
- Total PnL: -$1,271.67
- Current Equity: $3,000 - $1,271.67 = $1,728.33
- Invested: $65.33
- Cash: $1,728.33 - $65.33 = $1,663.00
- Invested %: ($65.33 / $1,728.33) * 100 = 3.8%
- Peak: $3,001.28 (if that was the max)
- Drawdown: ($1,728.33 - $3,001.28) / $3,001.28 = -42.4%

---

## Files to Modify

### For Max Drawdown Fix:
- `botreport/aws_daily_report.py:1245-1252` - Add starting equity to equity curve SQL
- `botreport/aws_daily_report.py:1225-1228` - Load `STARTING_EQUITY_USD` from env

### For Cash/Invested % Fix:
- `botreport/aws_daily_report.py:1297-1321` - Handle missing table, calculate from starting equity
- `.env` - Add `STARTING_EQUITY_USD=3000` (if not present)

### Documentation:
- This file: `docs/RISK_CAPITAL_METRICS_ISSUE.md`
