# Fix Critical PnL Calculation Bug

**Started:** 2025-11-19 20:28 UTC

## Session Overview

Fixing critical systematic PnL calculation bug discovered during hybrid order management testing. Database shows false profits (+$31-37 per symbol) when actual results are small losses (-$0.16 to -$0.43). All historical PnL data is currently unreliable.

## Context from Previous Session

**Bug Discovery:**
- During 8-hour live trading analysis, user noticed top performers "tied at $15.37" seemed suspicious
- User confirmed: "ZEC was negative on each trade as was DASH" according to Coinbase
- Investigation revealed ALL sells being matched to ancient parent BUYs from weeks/months ago

**Evidence:**
- DASH-USD: Selling at $75.88 today matched to Oct 4 BUY at $35.70 (error: +$31.84)
- ZEC-USD: Selling at $630.42 today matched to Oct 27 BUY at $366.52 (error: +$37.84)
- Pattern affects nearly all symbols in database

**Root Cause:**
1. Coinbase bracket orders (`trigger_bracket_gtc`) store parent_id metadata on their side
2. REST API reconciliation fetches order history with Coinbase's stale parent_ids
3. `reconcile_with_rest_api()` and `sync_open_orders()` blindly accept these parent_ids
4. Database gets incorrect FIFO matches, recent BUYs remain unused with full `remaining_size`

**Impact:**
- ALL historical PnL calculations are incorrect
- Win rate metrics unreliable
- Performance reports showing false profits
- Trading decisions based on bad data

## Goals

**Primary Objective:** Fix PnL calculation to accurately reflect actual trading performance

**Three-Part Fix Strategy:**

1. **Fix Reconciliation Code (Highest Priority)**
   - Modify `webhook/listener.py` reconciliation functions
   - Ignore Coinbase parent_ids for SELL orders
   - Force `parent_id = None` to let FIFO logic compute correct parents
   - Target functions:
     - `reconcile_with_rest_api()` (around line 1170-1195)
     - `sync_open_orders()` (around line 1244+)

2. **Fix Function Name Typo**
   - File: `webhook/websocket_market_manager.py:326`
   - Change: `find_unlinked_buy_id` → `find_latest_unlinked_buy_id`
   - This function doesn't exist, causing AttributeError caught by exception handler

3. **Run Maintenance to Recompute Historical Data**
   - Execute: `trade_recorder.fix_unlinked_sells()`
   - Recomputes FIFO linkages for all sells
   - Updates `parent_ids`, `cost_basis_usd`, `pnl_usd` correctly
   - Verify fix with SQL queries

**Success Criteria:**
- Recent sells matched to recent buys (same-day trades)
- DASH-USD and ZEC-USD show small losses instead of large profits
- Manual PnL calculation matches database values
- `remaining_size` properly decremented on parent BUYs

## Progress

### Update - 2025-11-19 20:28 UTC

**Summary**: Session initialized - Ready to implement three-part fix

**Current Git Status:**
- Branch: `claude/parameter-tuning-reports-011CV4hhiR6CNdTgBUPLGM5u`
- Modified: 1 file (Daily Trading Bot Report.eml)
- Untracked: Session files, diagnostic scripts

**Files to Modify:**
1. `webhook/listener.py` - Reconciliation logic
2. `webhook/websocket_market_manager.py` - Function name fix
3. Database - Run maintenance script

**Next Steps:**
1. Review reconciliation code structure
2. Implement fix to ignore Coinbase parent_ids
3. Fix function name typo
4. Create/run maintenance script
5. Verify with SQL queries on DASH-USD and ZEC-USD
6. Commit fixes

