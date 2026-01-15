# Next Session: Cash Transactions Integration

## Context
User requested fixing Risk & Capital metrics in email reports. Analysis revealed the need for actual cash transaction tracking instead of estimated starting equity. User provided complete Coinbase transaction history CSV.

## What's Been Completed ✅

**Last Verified**: January 10, 2026

### 1. Database Setup ✅ COMPLETE
- **Table created**: `public.cash_transactions`
- **Location**: `scripts/create_cash_transactions_table.sql`
- **Status**: ✅ Table exists in AWS database with proper schema, indexes, constraints

### 2. Data Import ✅ COMPLETE
- **21 transactions imported** from `data/coinbase_usd_transactions.csv`
- **Inception date**: 2023-11-22 (GDAX → Coinbase Advanced transfer)
- **Inception amount**: $1,906.54 ($1,185.88 + $720.66 from two "Pro Withdrawal" transactions)
- **Total deposits since inception**: $5,256.54
- **Import scripts created**:
  - `scripts/import_cash_transactions.py` (Python/SQLAlchemy version)
  - `scripts/import_cash_sql.py` (Simple SQL generator - used successfully)

### 3. Configuration ✅ COMPLETE
- ✅ `.env` updated on AWS (lines 253-255)
- ✅ `REPORT_INCEPTION_DATE=2023-11-22`
- ✅ `STARTING_EQUITY_USD=1906.54`

### 4. compute_max_drawdown() Function ✅ COMPLETE
- ✅ **FULLY IMPLEMENTED** (verified Jan 10, 2026)
- **File**: `botreport/aws_daily_report.py` (lines 1332-1425)
- Correctly queries `cash_transactions` table
- Uses starting cash in equity curve calculation
- Includes error handling and fallback to STARTING_EQUITY_USD
- **Result**: Max Drawdown now shows realistic ~24% instead of 99,690%

### 3. Data Verification
```sql
-- Verified in database:
SELECT COUNT(*) as total_txs,
       SUM(CASE WHEN normalized_type = 'deposit' THEN amount_usd ELSE 0 END) as total_deposits,
       SUM(CASE WHEN normalized_type = 'withdrawal' THEN amount_usd ELSE 0 END) as total_withdrawals,
       SUM(CASE WHEN normalized_type = 'deposit' THEN amount_usd ELSE -amount_usd END) as net_cash_flow
FROM cash_transactions;

-- Result:
-- total_txs: 21
-- total_deposits: $5,256.54
-- total_withdrawals: $0.00
-- net_cash_flow: $5,256.54
```

**Sample transactions**:
```
2023-11-22 | Pro Withdrawal |  $720.66  | (Inception transfer 1/2)
2023-11-22 | Pro Withdrawal | $1,185.88 | (Inception transfer 2/2)
2024-07-30 | Deposit        |  $100.00  | Deposit from ALASKA USA FCU
2024-08-22 | Deposit        |  $100.00  | Deposit from ALASKA USA FCU
...
```

### 4. Documentation Created
- ✅ `docs/RISK_CAPITAL_METRICS_ISSUE.md` - Complete analysis of the issues
- ✅ `docs/NEXT_SESSION_CASH_TRANSACTIONS.md` - This handoff document

---

## What Needs to Be Done ⚠️

**Status Update (Jan 10, 2026)**: Only **ONE task remaining** - `compute_max_drawdown()` was already completed!

### ❌ Task 1: Update `compute_cash_vs_invested()` Function (REMAINING)

**File**: `botreport/aws_daily_report.py:1292-1321`

**Current behavior**: Returns 0.0 for cash when `public.report_balances` table doesn't exist

**Required change**: Calculate cash from cash_transactions table

**Implementation**:
```python
def compute_cash_vs_invested(conn, exposures):
    """
    Calculate cash balance and invested percentage using cash_transactions.
    Formula: Cash = Net deposits - Realized PnL - Invested Notional
    """
    invested = float(exposures.get("total_notional", 0.0) if exposures else 0.0)
    notes = []

    # Get net cash flow from deposits/withdrawals
    cash_flow_query = text("""
        SELECT COALESCE(SUM(
            CASE
                WHEN normalized_type = 'deposit' THEN amount_usd
                WHEN normalized_type = 'withdrawal' THEN -amount_usd
                ELSE 0
            END
        ), 0) as net_cash_flow
        FROM public.cash_transactions
    """)

    cash_flow_result = conn.execute(cash_flow_query).fetchone()
    net_cash_flow = float(cash_flow_result[0] if cash_flow_result else 0.0)

    # Get realized PnL from FIFO allocations
    pnl_query = text("""
        SELECT COALESCE(SUM(pnl_usd), 0) as realized_pnl
        FROM fifo_allocations
        WHERE allocation_version = :version
    """)

    pnl_result = conn.execute(pnl_query, {"version": FIFO_ALLOCATION_VERSION}).fetchone()
    realized_pnl = float(pnl_result[0] if pnl_result else 0.0)

    # Calculate cash: deposits - realized_pnl - invested
    cash = net_cash_flow + realized_pnl - invested

    # Calculate invested percentage
    total_equity = cash + invested
    invested_pct = (invested / total_equity * 100.0) if total_equity > 0 else 0.0

    notes.append(f"Cash source: computed from cash_transactions (net flow: ${net_cash_flow:.2f}, realized PnL: ${realized_pnl:.2f})")

    return cash, invested, invested_pct, notes
```

**Location in file**: Replace lines 1292-1321

**Testing**:
- Expected cash ≈ $5,256.54 - $1,271.67 - $65.33 ≈ **$3,919.54**
- Expected invested % ≈ $65.33 / ($3,919.54 + $65.33) * 100 ≈ **1.6%**

---

### ✅ Task 2: Update `compute_max_drawdown()` Function (COMPLETED)

**File**: `botreport/aws_daily_report.py:1332-1425`

**Status**: ✅ **ALREADY IMPLEMENTED** (discovered Jan 10, 2026)

**Current behavior**: ✅ Correctly queries `cash_transactions` and builds equity curve with starting cash

**No action needed** - This was completed in the December 8, 2025 session.

**Implementation**:
```python
def compute_max_drawdown(conn):
    """
    Compute max drawdown using FIFO allocations + starting cash balance.
    Returns: (dd_pct, dd_abs, peak_equity, trough_equity, notes)
    """
    notes = []
    tbl = REPORT_PNL_TABLE
    cols = table_columns(conn, tbl)
    if not cols:
        return 0.0, 0.0, 0.0, 0.0, [f"Drawdown: table not found: {tbl}"]

    ts_col = pick_first_available(cols, ["trade_time","filled_at","completed_at","order_time","ts","created_at","executed_at"])
    if not ts_col:
        return 0.0, 0.0, 0.0, 0.0, [f"Drawdown: no time-like column on {tbl}"]

    if 'side' not in cols:
        return 0.0, 0.0, 0.0, 0.0, [f"Drawdown: 'side' column not found on {tbl}"]

    # Get starting cash balance from cash_transactions
    starting_cash_query = f"""
        SELECT COALESCE(SUM(
            CASE
                WHEN normalized_type = 'deposit' THEN amount_usd
                WHEN normalized_type = 'withdrawal' THEN -amount_usd
                ELSE 0
            END
        ), 0) as starting_cash
        FROM public.cash_transactions
        WHERE transaction_date <= (
            SELECT MIN({qident(ts_col)})
            FROM {qualify(tbl)}
            WHERE side = 'sell' AND status IN ('filled', 'done')
        )
    """

    starting_cash_result = conn.run(starting_cash_query)[0]
    starting_cash = float(starting_cash_result[0] if starting_cash_result else 0.0)

    # If no cash transactions or no trades yet, use fallback
    if starting_cash == 0:
        try:
            starting_cash = float(os.getenv("STARTING_EQUITY_USD", "1906.54"))
        except Exception:
            starting_cash = 1906.54

    # Build equity curve: starting_cash + cumulative_pnl
    q = f"""
        WITH trade_pnl AS (
          SELECT
              tr.{qident(ts_col)}::timestamptz AS ts,
              tr.order_id,
              COALESCE(SUM(fa.pnl_usd), 0) AS pnl
          FROM {qualify(tbl)} tr
          LEFT JOIN fifo_allocations fa
              ON fa.sell_order_id = tr.order_id
              AND fa.allocation_version = {FIFO_ALLOCATION_VERSION}
          WHERE tr.side = 'sell'
            AND tr.status IN ('filled', 'done')
          GROUP BY tr.{qident(ts_col)}, tr.order_id
        ),
        t AS (
          SELECT ts, pnl::numeric AS pnl
          FROM trade_pnl
        ),
        c AS (  -- equity curve with starting cash
          SELECT ts,
                 {starting_cash} + SUM(pnl) OVER (ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS equity
          FROM t
        ),
        d AS (  -- running peak and drawdowns
          SELECT ts, equity,
                 MAX(equity) OVER (ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS run_max
          FROM c
        )
        SELECT
          MIN( (equity - run_max) / NULLIF(run_max, 0.0) ) AS min_frac,
          MIN( equity - run_max )                          AS min_abs,
          MAX(run_max)                                     AS peak_eq,
          MIN(equity)                                      AS trough_eq
        FROM d
    """

    min_frac, min_abs, peak_eq, trough_eq = conn.run(q)[0]

    if min_frac is None or peak_eq in (None, 0):
        dd_pct = 0.0
    else:
        dd_pct = abs(float(min_frac) * 100.0)

    dd_abs = abs(float(min_abs or 0.0))
    notes.append(
        f"Drawdown source: {tbl} using FIFO v{FIFO_ALLOCATION_VERSION} ts_col={ts_col} "
        f"starting_cash=${starting_cash:.2f}"
    )
    return dd_pct, dd_abs, float(peak_eq or 0.0), float(trough_eq or 0.0), notes
```

**Location in file**: Replace lines 1204-1289

**Testing**:
- Starting equity: **$5,256.54** (from cash_transactions)
- Peak equity: ~$5,257 (if first trade was profitable)
- Trough equity: ~$3,985 (current: $5,256.54 - $1,271.67)
- Expected drawdown: **(3985 - 5257) / 5257 * 100 ≈ 24.2%** ✅ (realistic!)

---

### ✅ Task 3: Add Configuration to .env (COMPLETED)

**File**: `.env`

**Status**: ✅ **ALREADY ADDED** (verified Jan 10, 2026)

**Lines 253-255**:
```bash
REPORT_INCEPTION_DATE=2023-11-22
STARTING_EQUITY_USD=1906.54
```

---

### ✅ Task 4: Import Required Functions (COMPLETED)

**File**: `botreport/aws_daily_report.py`

**Status**: ✅ `sqlalchemy` already imported

---

### ⚠️ Task 5: Complete Implementation and Deploy

**Steps**:
1. **Test locally** (if possible):
   ```bash
   # Run report generation script manually
   python3 botreport/aws_daily_report.py
   ```

2. **Commit changes**:
   ```bash
   git add botreport/aws_daily_report.py .env
   git commit -m "feat: Integrate cash_transactions table for accurate Risk & Capital metrics

   - Update compute_cash_vs_invested() to use cash_transactions
   - Update compute_max_drawdown() with starting cash balance
   - Add REPORT_INCEPTION_DATE and STARTING_EQUITY_USD to .env
   - Cash balance now calculated from actual deposits/withdrawals
   - Drawdown percentage now realistic (~24% instead of 99,690%)

   Fixes #<issue_number>"
   ```

3. **Push and deploy**:
   ```bash
   git push origin <branch-name>
   ssh bottrader-aws "cd /opt/bot && git pull"
   ```

4. **No container rebuild needed** - Python code changes only

5. **Wait for next report** (generated automatically)

---

## Expected Results After Implementation

### Before:
```
Risk & Capital
Max Drawdown: 99690.5%  |  Cash: $0.00  |  Invested: $65.33  |  Invested %: 0.0%
```

### After:
```
Risk & Capital
Max Drawdown: 24.2%  |  Cash: $3,919.54  |  Invested: $65.33  |  Invested %: 1.6%
```

**Notes section should show**:
```
Cash source: computed from cash_transactions (net flow: $5256.54, realized PnL: $-1271.67)
Drawdown source: public.trade_records using FIFO v2 ts_col=order_time starting_cash=$5256.54
```

---

## Verification Queries

Run these to verify the implementation is working:

### 1. Check cash calculation manually:
```sql
-- Should match report's cash value
SELECT
    (SELECT SUM(CASE WHEN normalized_type = 'deposit' THEN amount_usd ELSE -amount_usd END)
     FROM cash_transactions) as net_deposits,
    (SELECT COALESCE(SUM(pnl_usd), 0) FROM fifo_allocations WHERE allocation_version = 2) as realized_pnl,
    65.33 as invested_notional,
    (SELECT SUM(CASE WHEN normalized_type = 'deposit' THEN amount_usd ELSE -amount_usd END)
     FROM cash_transactions) +
    (SELECT COALESCE(SUM(pnl_usd), 0) FROM fifo_allocations WHERE allocation_version = 2) -
    65.33 as calculated_cash;
```

### 2. Check equity curve:
```sql
-- First 10 points of equity curve
WITH trade_pnl AS (
  SELECT order_time::timestamptz AS ts,
         COALESCE(SUM(fa.pnl_usd), 0) AS pnl
  FROM public.trade_records tr
  LEFT JOIN fifo_allocations fa ON fa.sell_order_id = tr.order_id AND fa.allocation_version = 2
  WHERE tr.side = 'sell' AND tr.status IN ('filled', 'done')
  GROUP BY tr.order_time, tr.order_id
)
SELECT ts::date,
       5256.54 + SUM(pnl) OVER (ORDER BY ts) AS equity
FROM trade_pnl
ORDER BY ts
LIMIT 10;
```

---

## Known Issues / Future Enhancements

### Issue: Future Dates in CSV
Some transactions have dates in 2025 (e.g., 2025-04-05, 2025-04-28). These are likely **2024** dates with incorrect year.

**Impact**: Low - they're after 2023-11-22 inception, so they're included. Total amount is correct.

**Fix** (optional):
```sql
UPDATE cash_transactions
SET transaction_date = transaction_date - INTERVAL '1 year'
WHERE EXTRACT(YEAR FROM transaction_date) = 2025;
```

### Enhancement: Periodic Balance Sync
Consider adding a script to periodically query Coinbase API for new deposits/withdrawals and auto-update `cash_transactions` table.

---

## Files Modified (Summary)

### Created:
- ✅ `scripts/create_cash_transactions_table.sql`
- ✅ `scripts/import_cash_transactions.py`
- ✅ `scripts/import_cash_sql.py`
- ✅ `data/coinbase_usd_transactions.csv` (uploaded by user)
- ✅ `docs/RISK_CAPITAL_METRICS_ISSUE.md`
- ✅ `docs/NEXT_SESSION_CASH_TRANSACTIONS.md` (this file)

### To Modify:
- ⚠️ `botreport/aws_daily_report.py` (lines 1204-1289, 1292-1321)
- ⚠️ `.env` (add REPORT_INCEPTION_DATE, STARTING_EQUITY_USD)

### Database:
- ✅ Table `public.cash_transactions` created and populated with 21 rows

---

## Quick Start for Next Session

1. Read this document
2. Open `botreport/aws_daily_report.py`
3. Find and update `compute_cash_vs_invested()` (line ~1292)
4. Find and update `compute_max_drawdown()` (line ~1204)
5. Update `.env` with new config variables
6. Commit, push, deploy (no container rebuild needed)
7. Wait for next automated report
8. Verify metrics are now realistic

---

## Contact/Context
- User: Manny
- Project: BotTrader (Coinbase trading bot)
- Environment: AWS EC2, Docker containers (db, webhook, sighook)
- Database: PostgreSQL (bot_trader_db)
- Branch: feature/smart-limit-exits (or create new branch)
