# Next Session: FIFO Integration Implementation Plan

**Date Created:** 2025-11-29
**Current Branch:** feature/smart-limit-exits
**Target Branch:** feature/fifo-incremental-computation (new)

---

## Session Summary (What We Discussed)

### Key Decisions Made

1. **Follow FIFO_ALLOCATIONS_DESIGN.md architecture** - Batch/incremental computation approach
2. **Don't store computed PnL in trade_records** - Keep only immutable facts
3. **Run incremental FIFO every 5 minutes via cron** - Near real-time without coupling
4. **Reports already use FIFO v2** - USE_FIFO_ALLOCATIONS=1 is already set

### Critical Files Analyzed

- `CRITICAL_BUG_ANALYSIS_remaining_size.md` - Documents $61.59 PnL discrepancy
- `email_report_verification_results.md` - Confirms FIFO v2 accuracy when up-to-date
- `docs/FIFO_ALLOCATIONS_DESIGN.md` - Architectural blueprint (lines 72-74: NO parent_id, NO pnl_usd)
- `SharedDataManager/trade_recorder.py` - Lines 257-311: Current inline FIFO computation (to be removed)
- `botreport/aws_daily_report.py` - Lines 722-724: Already using FIFO allocations

---

## What Gets Replaced

| trade_records Field | Status | Replacement |
|---------------------|--------|-------------|
| `realized_profit` | ❌ Deprecate (set NULL) | `SUM(fifo_allocations.pnl_usd)` |
| `pnl_usd` | ❌ Deprecate (set NULL) | `SUM(fifo_allocations.pnl_usd)` |
| `cost_basis_usd` | ❌ Deprecate (set NULL) | `SUM(fifo_allocations.cost_basis_usd)` |
| `sale_proceeds_usd` | ❌ Deprecate (set NULL) | `SUM(fifo_allocations.proceeds_usd)` |
| `parent_id` | ❌ Deprecate (set NULL) | `fifo_allocations.buy_order_id` (can be multiple!) |
| `remaining_size` (SELLs) | ❌ Always NULL | N/A |
| `remaining_size` (BUYs) | ✅ Keep | Updated by FIFO engine only |

---

## Implementation Tasks

### Phase 1: Preparation (New Session Start)

#### Task 1.1: Create New Branch ✅
```bash
# Commit or stash current work
git add CRITICAL_BUG_ANALYSIS_remaining_size.md email_report_verification_results.md verify_email_report.py
git commit -m "docs: Add FIFO bug analysis and email report verification"

# Create new feature branch from main
git checkout main
git pull
git checkout -b feature/fifo-incremental-computation
```

#### Task 1.2: Clean Working Directory
```bash
# Move analysis docs to docs/ folder
git mv CRITICAL_BUG_ANALYSIS_remaining_size.md docs/
git mv email_report_verification_results.md docs/
git mv verify_email_report.py scripts/

git commit -m "chore: Organize FIFO analysis documentation"
```

---

### Phase 2: Add Incremental Mode to FIFO Script

**File:** `scripts/compute_allocations.py`

**Changes Needed:**

1. Add command-line arguments:
   ```python
   parser.add_argument(
       '--incremental',
       action='store_true',
       help='Only process trades since last computation (faster)'
   )

   parser.add_argument(
       '--since',
       type=str,
       help='Process trades since this time (e.g., "10 minutes ago", "2025-11-28 10:00")'
   )
   ```

2. Implement incremental logic:
   ```python
   async def compute_incremental(engine, since_time, version):
       """
       Compute allocations only for trades since a given time.
       Uses existing inventory state from previous computation.
       """
       # 1. Get last computation time (from fifo_computation_log or fifo_allocations)
       # 2. Load inventory snapshot (or compute from existing allocations)
       # 3. Fetch only NEW trades since last run
       # 4. Process new trades with existing inventory
       # 5. Insert new allocations (same version, extending)
   ```

**Test Plan:**
- Run full computation once to establish baseline
- Run incremental with `--since "5 minutes ago"`
- Verify allocations are created for new trades only
- Verify PnL matches full recomputation

---

### Phase 3: Modify trade_recorder.py

**File:** `SharedDataManager/trade_recorder.py`

**Lines to Modify:** 257-311

**Current Code (Simplified):**
```python
if side == "sell":
    # Check if enough inventory
    # Call compute_cost_basis_and_sale_proceeds()
    # Get parent_ids, pnl_usd, cost_basis_usd, etc.
    # Apply update_instructions to remaining_size (BUG: doesn't persist)
```

**New Code:**
```python
if side == "sell":
    # Just record the trade facts, don't compute PnL
    parent_id = None
    pnl_usd = None
    cost_basis_usd = None
    sale_proceeds_usd = None
    net_sale_proceeds_usd = None

    self.logger.info(
        f"📝 SELL recorded: {symbol} {amount}@{price}. "
        f"PnL will be computed by FIFO engine (cron job)."
    )
```

**Benefits:**
- Simpler, faster trade recording
- No risk of FIFO bugs affecting live trading
- Clear separation of concerns

**Risks:**
- PnL not available immediately (5-15 min delay)
- Need to ensure cron job is reliable

**Mitigation:**
- Add monitoring to alert if FIFO cron fails
- Include FIFO health metrics in email report (last allocation time)

---

### Phase 4: Setup Cron Job on AWS

**Commands:**
```bash
# SSH to AWS server
ssh bottrader-aws

# Edit crontab
crontab -e

# Add these lines:
# Incremental FIFO every 5 minutes
*/5 * * * * cd /opt/bot && docker exec sighook python3 -m scripts.compute_allocations --incremental --since "10 minutes ago" >> /var/log/fifo_incremental.log 2>&1

# Full FIFO daily at 2 AM UTC (validation + cleanup old versions)
0 2 * * * cd /opt/bot && docker exec sighook python3 -m scripts.compute_allocations --version 2 --all-symbols --force >> /var/log/fifo_full.log 2>&1

# Save and exit
```

**Verify Cron Setup:**
```bash
# Check cron is running
sudo service cron status

# Watch incremental log
tail -f /var/log/fifo_incremental.log

# Check allocations are being created
docker exec -t db psql -U bot_user -d bot_trader_db -c "
SELECT
    MAX(allocation_time) as last_allocation,
    COUNT(*) as allocations_last_hour,
    allocation_version
FROM fifo_allocations
WHERE allocation_time >= NOW() - INTERVAL '1 hour'
GROUP BY allocation_version;
"
```

---

### Phase 5: Update Email Report (Add FIFO Health Metrics)

**File:** `botreport/aws_daily_report.py`

**Enhancement:** Add FIFO health section showing:
- Last allocation time (should be within 5-10 minutes)
- Allocations created in last 24 hours
- Any unmatched sells
- FIFO computation status

**Example Addition:**
```python
def query_fifo_health_detailed(conn, version: int):
    """Get detailed FIFO health metrics for report."""
    q = f"""
    SELECT
        MAX(allocation_time) as last_allocation_time,
        COUNT(*) FILTER (WHERE allocation_time >= NOW() - INTERVAL '1 hour') as allocations_last_hour,
        COUNT(*) FILTER (WHERE allocation_time >= NOW() - INTERVAL '24 hours') as allocations_last_day,
        COUNT(*) FILTER (WHERE buy_order_id IS NULL) as unmatched_sells
    FROM fifo_allocations
    WHERE allocation_version = {version}
    """
    result = conn.run(q)[0]

    return {
        'last_allocation_time': result[0],
        'allocations_last_hour': result[1],
        'allocations_last_day': result[2],
        'unmatched_sells': result[3],
    }
```

---

### Phase 6: Testing & Validation

#### Test 1: Incremental Mode Works
```bash
# Run full computation
docker exec sighook python3 -m scripts.compute_allocations --version 2 --all-symbols --force

# Wait 5 minutes, make a test trade

# Run incremental
docker exec sighook python3 -m scripts.compute_allocations --incremental --since "10 minutes ago"

# Verify allocation created for test trade
```

#### Test 2: Modified trade_recorder Works
```bash
# Deploy modified trade_recorder.py
# Make a test sell trade
# Verify trade_record created with NULL pnl_usd
# Wait for cron to run
# Verify FIFO allocation created
```

#### Test 3: Email Report Accurate
```bash
# Generate email report
python3 -m botreport.aws_daily_report

# Verify:
# - PnL matches FIFO allocations
# - FIFO health metrics show recent allocation time
# - No warnings about stale data
```

---

## Rollback Plan

If issues arise:

1. **Revert trade_recorder.py changes**
   ```bash
   git checkout main -- SharedDataManager/trade_recorder.py
   ```

2. **Stop cron job**
   ```bash
   crontab -e  # Comment out FIFO lines
   ```

3. **Fall back to old PnL fields**
   ```bash
   # In .env
   USE_FIFO_ALLOCATIONS=0
   ```

Old `parent_id` and `realized_profit` fields will still be there, so reports will work (though with bugs).

---

## Success Criteria

✅ Incremental FIFO runs every 5 minutes without errors
✅ Trade recording works without computing PnL inline
✅ Email reports show correct PnL from FIFO allocations
✅ FIFO health metrics indicate system is healthy
✅ No performance degradation in trade recording
✅ All tests pass

---

## Files to Modify

1. `scripts/compute_allocations.py` - Add --incremental mode
2. `SharedDataManager/trade_recorder.py` - Remove inline FIFO computation
3. `botreport/aws_daily_report.py` - Add FIFO health metrics (optional)
4. AWS crontab - Add incremental FIFO job

## Files to Create

1. `scripts/test_fifo_incremental.py` - Test incremental mode
2. `/var/log/fifo_incremental.log` - Incremental FIFO log (on AWS)
3. `/var/log/fifo_full.log` - Full FIFO log (on AWS)

---

## Questions to Resolve in Next Session

1. Should we add feature flag to control inline vs cron FIFO? (For gradual rollout)
2. Should we add monitoring/alerting if FIFO cron fails?
3. Should we implement inventory snapshots now or later? (Performance optimization)
4. How to handle existing trades with old PnL values? (Leave them or backfill?)

---

## References

- **Design Document:** `docs/FIFO_ALLOCATIONS_DESIGN.md`
- **Bug Analysis:** `CRITICAL_BUG_ANALYSIS_remaining_size.md` (move to docs/)
- **Verification:** `email_report_verification_results.md` (move to docs/)
- **Current FIFO Engine:** `fifo_engine/engine.py`
- **Current FIFO Script:** `scripts/compute_allocations.py`

---

## Branch Strategy

```
main
  └─ feature/fifo-incremental-computation (NEW - start here)
       └─ Phase 1: Add --incremental mode
       └─ Phase 2: Modify trade_recorder.py
       └─ Phase 3: Setup cron job
       └─ Phase 4: Testing & validation
       └─ Merge to main when stable
```

---

**Status:** Ready to implement
**Estimated Time:** 4-6 hours (across multiple sessions)
**Risk Level:** Medium (affecting core PnL calculation)
**Mitigation:** Parallel operation, easy rollback, comprehensive testing

---

**Next Step:** Start new Claude Code session, checkout new branch, begin Phase 1
