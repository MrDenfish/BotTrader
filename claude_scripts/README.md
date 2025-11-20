# CLANKER Position Bug - Complete Fix Guide

## Overview

**Problem**: BUY orders are recording `remaining_size = original order size` instead of `0`, causing the position calculation to only count SELLS, resulting in a phantom short position.

**Current Impact**: 
- CLANKER shows -243.89 position (should be ~0)
- Total portfolio notional shows ~$100k (should be ~$192)
- Win rate and PnL calculations are affected

## Quick Start

### Option A: Automated Fix (Recommended)

If you're on your bot server:

```bash
# 1. Copy all scripts to your BotTrader directory
scp *.sql apply_all_fixes.sh YOUR_SERVER:/opt/bot/

# 2. SSH to server
ssh YOUR_SERVER

# 3. Go to BotTrader directory
cd /opt/bot

# 4. Run the master fix script
./apply_all_fixes.sh
```

This will:
1. Investigate and document the current state
2. Fix the bad data in trade_records
3. Update the views defensively
4. Verify the fixes worked
5. Generate a test report

### Option B: Manual Fix (Step by Step)

#### Step 1: Investigation (Optional but Recommended)

```bash
docker compose -f docker-compose.aws.yml exec -T db psql -U bot_user -d bot_trader_db < investigate_bug.sql > investigation_results.txt
less investigation_results.txt
```

This shows you:
- Current problematic records
- Scope of the bug
- What positions currently look like
- What they should look like

#### Step 2: Quick Data Fix (CRITICAL)

This fixes all existing bad records:

```bash
docker compose -f docker-compose.aws.yml exec -T db psql -U bot_user -d bot_trader_db < fix_remaining_size_bug.sql > fix_results.txt
```

Or interactively:

```bash
docker compose -f docker-compose.aws.yml exec db psql -U bot_user -d bot_trader_db
```

Then paste the UPDATE query:

```sql
UPDATE trade_records
SET remaining_size = 0
WHERE status = 'filled'
  AND side = 'buy'
  AND remaining_size = size
  AND remaining_size IS NOT NULL;

-- Verify
SELECT * FROM report_positions WHERE symbol = 'CLANKER-USD';
```

#### Step 3: Update Views (Defensive Fix)

This makes the views handle both correct and incorrect data:

```bash
docker compose -f docker-compose.aws.yml exec -T db psql -U bot_user -d bot_trader_db < fix_report_views.sql
```

#### Step 4: Verify the Fix

```bash
docker compose -f docker-compose.aws.yml exec db psql -U bot_user -d bot_trader_db -c "
SELECT 
    symbol,
    position_qty,
    avg_entry_price,
    (position_qty * avg_entry_price) as notional_usd
FROM report_positions
ORDER BY ABS(position_qty * avg_entry_price) DESC
LIMIT 10;
"
```

**Expected Results:**
- CLANKER position should be near 0 (maybe tiny dust like 0.0002)
- Total notional should be ~$192
- Top positions: ATOM (~$44), DASH (~$43), TOWNS (~$44)

#### Step 5: Test Report

```bash
docker compose -f docker-compose.aws.yml run --rm report-job python -m botreport --hours 24
```

Check that the email shows:
- Invested Notional: ~$192 ✅
- No phantom $100k positions
- Reasonable win rates

## Finding the Root Cause in Code

After fixing the data, you need to fix the code so new trades don't have this bug:

### Search for the Problem

```bash
cd /path/to/BotTrader
./find_remaining_size_code.sh
```

Or manually:

```bash
# Find where remaining_size is set
grep -rn "remaining_size.*=" --include="*.py" . | grep -v test

# Find TradeRecorder
find . -name "*recorder*.py" | grep -v __pycache__

# Find webhook handlers
find . -name "*webhook*.py" -o -name "*order*.py" | grep -v __pycache__
```

### What to Look For

**BAD CODE (Current Bug):**

```python
# This is wrong - it sets remaining_size to filled_size for completed orders
remaining_size = order.get("filled_size")
# or
remaining_size = order.get("size")
```

**CORRECT CODE:**

```python
# For filled orders, remaining_size should be 0 or NULL
if order.get("status") == "FILLED":
    remaining_size = 0  # or None
else:
    remaining_size = order.get("remaining_size", 0)
```

### Likely Files to Check

1. `*recorder*.py` - TradeRecorder class
2. `*webhook*.py` - Webhook handlers for order updates
3. `*order*.py` - Order fill handlers
4. Any file with database INSERT/UPDATE for trade_records

## Files Included

| File | Purpose |
|------|---------|
| `investigate_bug.sql` | Diagnose the problem (run first) |
| `fix_remaining_size_bug.sql` | Fix bad data in trade_records |
| `fix_report_views.sql` | Update views to handle bad data |
| `apply_all_fixes.sh` | Master script - runs everything |
| `find_remaining_size_code.sh` | Search for code to fix |
| `README.md` | This file |

## Verification Checklist

After applying all fixes:

- [ ] Run investigation script and review results
- [ ] Apply data fix (UPDATE query)
- [ ] CLANKER position is near 0 (not -243.89)
- [ ] Total notional is ~$192 (not ~$100k)
- [ ] Top positions show ATOM, DASH, TOWNS (~$40-50 each)
- [ ] Apply view fix
- [ ] Generate test report - check invested notional
- [ ] Find and fix code that sets remaining_size
- [ ] Monitor next scheduled report

## Troubleshooting

### "CLANKER position still shows -243.89"

The UPDATE query didn't work. Check:

```sql
-- See if there are actually rows to fix
SELECT COUNT(*) FROM trade_records
WHERE status = 'filled'
  AND side = 'buy'
  AND remaining_size = size;
```

If count is 0, the view definition might be the issue. Run `fix_report_views.sql`.

### "Total notional is still huge"

Check if there are other symbols with phantom positions:

```sql
SELECT * FROM report_positions 
ORDER BY ABS(position_qty * avg_entry_price) DESC;
```

Look for positions that shouldn't exist or seem way too large.

### "Code fix - where is TradeRecorder?"

```bash
find . -type f -name "*.py" -exec grep -l "class.*Trade.*Record" {} \;
```

### "Docker command not found"

Make sure you're in the directory with `docker-compose.aws.yml` and Docker is running.

## Support

If you encounter issues:

1. Save all output files (investigation_results.txt, fix_results.txt, etc.)
2. Check verification_results.txt for what changed
3. Review the current positions with:
   ```sql
   SELECT * FROM report_positions ORDER BY ABS(position_qty * avg_entry_price) DESC;
   ```

## Summary

**What we're fixing:**
- **Bug**: Filled buy orders have `remaining_size = size` (should be 0)
- **Impact**: Only sells counted → phantom short positions
- **Fix**: Update bad data + fix views + fix code

**After fixes:**
- CLANKER: 0 position (not -243.89)
- Portfolio: ~$192 notional (not ~$100k)
- Reports: Accurate metrics

**Priority:**
1. Fix data NOW (apply_all_fixes.sh)
2. Verify it worked
3. Find and fix code (so it doesn't happen again)
