# FIFO Allocations Architecture - Design Document
**Version:** 1.0
**Date:** 2025-11-20
**Status:** Draft - Under Review

---

## Executive Summary

This document specifies a ground-up redesign of the PnL calculation system to address fundamental architectural flaws that cause cascading database corruption. The new architecture separates immutable trade facts from computed FIFO allocations, enabling verifiable and recomputable PnL calculations.

**Problem:** Current system stores computed values (parent_id, pnl_usd) as if they're immutable facts, but they depend on mutable state (remaining_size), causing unfixable corruption.

**Solution:** Separate concerns - trades are immutable ledger entries, allocations are computed separately and can be recomputed anytime.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Database Schema](#database-schema)
3. [FIFO Allocation Algorithm](#fifo-allocation-algorithm)
4. [Migration Strategy](#migration-strategy)
5. [API Design](#api-design)
6. [Validation & Invariants](#validation--invariants)
7. [Implementation Phases](#implementation-phases)
8. [Risk Analysis](#risk-analysis)

---

## Architecture Overview

### Core Principles

**1. Immutability**
- Trade records represent facts about what happened
- Once inserted, trade facts never change
- Updates only allowed for metadata (reconciliation timestamps, etc.)

**2. Separation of Concerns**
- **What happened:** `trade_records` table (source of truth)
- **What it means:** `fifo_allocations` table (derived computations)
- **Reports:** Join trades with allocations

**3. Recomputability**
- Allocations can be deleted and recomputed anytime
- Source of truth (trade records) remains intact
- Enables testing different allocation strategies

**4. Verifiability**
- Allocations must satisfy mathematical invariants
- Can validate that allocations sum correctly
- Can detect and report inconsistencies

### Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                     TRADE INGESTION                         │
│  (Websocket, REST API, Manual Entry)                        │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
         ┌───────────────────────────────┐
         │     trade_records table       │
         │   (Immutable Trade Facts)     │
         │                               │
         │ • order_id, symbol, side      │
         │ • size, price, timestamp      │
         │ • fees, source, status        │
         │                               │
         │ ❌ NO parent_id               │
         │ ❌ NO pnl_usd                 │
         │ ❌ NO remaining_size (for SELLs) │
         └───────────────┬───────────────┘
                         │
                         │ (Periodic batch job or on-demand)
                         ▼
         ┌───────────────────────────────┐
         │   FIFO Computation Engine     │
         │                               │
         │ • Process trades chronologically │
         │ • Match SELLs to BUYs (FIFO)  │
         │ • Track remaining inventory   │
         │ • Calculate cost basis & PnL  │
         └───────────────┬───────────────┘
                         │
                         ▼
         ┌───────────────────────────────┐
         │   fifo_allocations table      │
         │   (Computed Matches)          │
         │                               │
         │ • sell_order_id → buy_order_id │
         │ • allocated_size              │
         │ • cost_basis, proceeds, pnl   │
         │ • allocation_version          │
         └───────────────┬───────────────┘
                         │
                         ▼
         ┌───────────────────────────────┐
         │      PnL REPORTS & QUERIES    │
         │                               │
         │ • JOIN trades with allocations │
         │ • Aggregate by symbol/period  │
         │ • Real-time dashboards        │
         └───────────────────────────────┘
```

---

## Database Schema

### 1. trade_records (Modified)

**Changes:**
- Remove: `parent_id` column (computed, not stored)
- Remove: `parent_ids` column (computed, not stored)
- Remove: `pnl_usd` column (computed, not stored)
- Remove: `cost_basis_usd` column (computed, not stored)
- Remove: `sale_proceeds_usd` column (computed, not stored)
- Remove: `net_sale_proceeds_usd` column (computed, not stored)
- Remove: `realized_profit` column (computed, not stored)
- Keep: `remaining_size` for BUYs only (inventory tracking)

**Rationale:** These fields are derived from allocations. Storing them creates dual source of truth and enables corruption.

**Migration Strategy:** Keep old columns initially (parallel operation), mark as deprecated, eventually drop.

```sql
-- Core immutable fields (never updated after insert)
CREATE TABLE trade_records (
    order_id VARCHAR PRIMARY KEY,
    symbol VARCHAR NOT NULL,
    side VARCHAR NOT NULL CHECK (side IN ('buy', 'sell')),
    size DECIMAL NOT NULL CHECK (size > 0),
    price DECIMAL NOT NULL CHECK (price > 0),
    order_time TIMESTAMP WITH TIME ZONE NOT NULL,

    -- Fees and costs (immutable)
    total_fees_usd DECIMAL DEFAULT 0,

    -- Trade execution metadata (immutable)
    status VARCHAR,
    order_type VARCHAR,
    trigger JSONB,

    -- Source tracking (immutable after insert)
    source VARCHAR NOT NULL,  -- websocket | webhook | manual | reconciled
    ingest_via VARCHAR,        -- websocket | rest | manual | import

    -- Reconciliation metadata (updatable)
    last_reconciled_at TIMESTAMP WITH TIME ZONE,
    last_reconciled_via VARCHAR,

    -- Inventory tracking (for BUYs only, managed by allocation engine)
    remaining_size DECIMAL,  -- NULL for SELLs, managed for BUYs

    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_trade_records_symbol_time ON trade_records(symbol, order_time);
CREATE INDEX idx_trade_records_side_symbol ON trade_records(side, symbol);
CREATE INDEX idx_trade_records_order_time ON trade_records(order_time DESC);
```

### 2. fifo_allocations (New)

**Purpose:** Records how each SELL is matched to BUY(s) using FIFO logic.

```sql
CREATE TABLE fifo_allocations (
    id BIGSERIAL PRIMARY KEY,

    -- The match
    sell_order_id VARCHAR NOT NULL REFERENCES trade_records(order_id),
    buy_order_id VARCHAR NOT NULL REFERENCES trade_records(order_id),
    symbol VARCHAR NOT NULL,

    -- How much was allocated from this buy to this sell
    allocated_size DECIMAL NOT NULL CHECK (allocated_size > 0),

    -- Prices (denormalized for query performance)
    buy_price DECIMAL NOT NULL,
    sell_price DECIMAL NOT NULL,
    buy_fees_per_unit DECIMAL NOT NULL,  -- buy.total_fees_usd / buy.size
    sell_fees_per_unit DECIMAL NOT NULL, -- sell.total_fees_usd / sell.size

    -- Computed PnL for this allocation
    cost_basis_usd DECIMAL NOT NULL,     -- (buy_price + buy_fees_per_unit) * allocated_size
    proceeds_usd DECIMAL NOT NULL,       -- sell_price * allocated_size
    net_proceeds_usd DECIMAL NOT NULL,   -- proceeds_usd - (sell_fees_per_unit * allocated_size)
    pnl_usd DECIMAL NOT NULL,            -- net_proceeds_usd - cost_basis_usd

    -- Timestamps
    buy_time TIMESTAMP WITH TIME ZONE NOT NULL,
    sell_time TIMESTAMP WITH TIME ZONE NOT NULL,

    -- Allocation metadata
    allocation_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    allocation_version INT NOT NULL,     -- Increments on recomputation
    allocation_batch_id UUID,            -- Groups allocations computed together

    -- Constraints
    CHECK (buy_time <= sell_time),       -- Can't sell before buy
    CHECK (cost_basis_usd >= 0),
    CHECK (proceeds_usd >= 0)
);

-- Indexes for performance
CREATE INDEX idx_fifo_allocations_sell ON fifo_allocations(sell_order_id);
CREATE INDEX idx_fifo_allocations_buy ON fifo_allocations(buy_order_id);
CREATE INDEX idx_fifo_allocations_symbol ON fifo_allocations(symbol, allocation_version);
CREATE INDEX idx_fifo_allocations_version ON fifo_allocations(allocation_version);
CREATE INDEX idx_fifo_allocations_batch ON fifo_allocations(allocation_batch_id);

-- Unique constraint: One version can't allocate same buy→sell pair twice
CREATE UNIQUE INDEX idx_fifo_allocations_unique
    ON fifo_allocations(sell_order_id, buy_order_id, allocation_version);
```

**Key Design Decisions:**

1. **One row per buy→sell pair**: A sell that uses multiple buys gets multiple rows
2. **Denormalized prices**: Store prices here for query performance (avoid joins)
3. **Version tracking**: Enable parallel operation of old and new systems
4. **Batch ID**: Group allocations computed together for atomic updates

### 3. fifo_computation_log (New)

**Purpose:** Track allocation computation runs for debugging and auditing.

```sql
CREATE TABLE fifo_computation_log (
    id BIGSERIAL PRIMARY KEY,

    -- What was computed
    symbol VARCHAR,                      -- NULL = all symbols
    allocation_version INT NOT NULL,
    allocation_batch_id UUID NOT NULL,

    -- When and how
    computation_start TIMESTAMP WITH TIME ZONE NOT NULL,
    computation_end TIMESTAMP WITH TIME ZONE,
    computation_duration_ms INT,

    -- Results
    status VARCHAR NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'partial')),
    buys_processed INT DEFAULT 0,
    sells_processed INT DEFAULT 0,
    allocations_created INT DEFAULT 0,

    -- Errors
    error_message TEXT,
    error_traceback TEXT,

    -- Statistics
    symbols_processed VARCHAR[],
    total_pnl_computed DECIMAL,

    -- Configuration
    computation_mode VARCHAR,            -- 'full' | 'incremental' | 'symbol'
    triggered_by VARCHAR,                -- 'manual' | 'scheduled' | 'api'

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_fifo_computation_log_version ON fifo_computation_log(allocation_version);
CREATE INDEX idx_fifo_computation_log_batch ON fifo_computation_log(allocation_batch_id);
CREATE INDEX idx_fifo_computation_log_status ON fifo_computation_log(status, computation_start DESC);
```

### 4. fifo_inventory_snapshot (New - Optional)

**Purpose:** Periodic snapshots of inventory state for faster incremental computation.

```sql
CREATE TABLE fifo_inventory_snapshot (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR NOT NULL,
    buy_order_id VARCHAR NOT NULL REFERENCES trade_records(order_id),
    remaining_size DECIMAL NOT NULL,
    snapshot_time TIMESTAMP WITH TIME ZONE NOT NULL,
    allocation_version INT NOT NULL,

    UNIQUE(symbol, buy_order_id, allocation_version)
);

CREATE INDEX idx_inventory_snapshot_symbol_version
    ON fifo_inventory_snapshot(symbol, allocation_version, snapshot_time DESC);
```

---

## FIFO Allocation Algorithm

### Overview

Process all trades for a symbol chronologically, matching each SELL to available BUYs in FIFO (First-In-First-Out) order.

### Algorithm Pseudocode

```python
def compute_allocations_for_symbol(symbol: str, version: int) -> List[Allocation]:
    """
    Compute FIFO allocations for all trades of a given symbol.

    Returns list of allocations to be inserted into fifo_allocations table.
    """
    allocations = []

    # 1. Fetch all BUYs and SELLs chronologically
    buys = fetch_buys(symbol, order_by='order_time ASC')
    sells = fetch_sells(symbol, order_by='order_time ASC')

    # 2. Track available inventory (buy_order_id → remaining_size)
    inventory = {}
    for buy in buys:
        inventory[buy.order_id] = buy.size

    # 3. Process each SELL
    for sell in sells:
        remaining_to_allocate = sell.size

        # 4. Match to BUYs in FIFO order (oldest first)
        for buy in buys:
            if remaining_to_allocate <= 0:
                break

            available = inventory.get(buy.order_id, 0)
            if available <= 0:
                continue

            # 5. Allocate (partial or full)
            allocated = min(available, remaining_to_allocate)

            # 6. Create allocation record
            allocation = create_allocation(
                sell=sell,
                buy=buy,
                allocated_size=allocated,
                version=version
            )
            allocations.append(allocation)

            # 7. Update inventory
            inventory[buy.order_id] -= allocated
            remaining_to_allocate -= allocated

        # 8. Check for unmatched sells (shouldn't happen with correct data)
        if remaining_to_allocate > 0:
            log_warning(f"SELL {sell.order_id} has unmatched size: {remaining_to_allocate}")
            # Option: Create "unmatched" allocation record for visibility

    return allocations


def create_allocation(sell, buy, allocated_size, version) -> Allocation:
    """
    Create an allocation record with computed PnL.
    """
    # Fee allocation (proportional)
    buy_fees_per_unit = buy.total_fees_usd / buy.size
    sell_fees_per_unit = sell.total_fees_usd / sell.size

    # Cost basis
    cost_basis = (buy.price + buy_fees_per_unit) * allocated_size

    # Proceeds
    proceeds = sell.price * allocated_size
    net_proceeds = proceeds - (sell_fees_per_unit * allocated_size)

    # PnL
    pnl = net_proceeds - cost_basis

    return Allocation(
        sell_order_id=sell.order_id,
        buy_order_id=buy.order_id,
        symbol=sell.symbol,
        allocated_size=allocated_size,
        buy_price=buy.price,
        sell_price=sell.price,
        buy_fees_per_unit=buy_fees_per_unit,
        sell_fees_per_unit=sell_fees_per_unit,
        cost_basis_usd=cost_basis,
        proceeds_usd=proceeds,
        net_proceeds_usd=net_proceeds,
        pnl_usd=pnl,
        buy_time=buy.order_time,
        sell_time=sell.order_time,
        allocation_version=version
    )
```

### Edge Cases

**1. Partial Fills**
- One SELL may use multiple BUYs
- One BUY may be used by multiple SELLs
- Each pair gets its own allocation record

**2. Unmatched SELLs**
- Shouldn't happen with correct data (can't sell what wasn't bought)
- If detected: log warning, optionally create placeholder allocation
- Reports should flag these for investigation

**3. Time Ordering Issues**
- Use `order_time` from Coinbase as source of truth
- Ignore local insert/update times
- If two trades have same timestamp, order by order_id (deterministic)

**4. Symbols with No Activity**
- Skip symbols with only BUYs (no SELLs to allocate)
- Still process to validate inventory tracking

---

## Migration Strategy

### Phase 1: Parallel Operation (Weeks 1-2)

**Goal:** Run both systems side-by-side, validate new system correctness.

**Steps:**
1. Create new tables (fifo_allocations, fifo_computation_log)
2. Keep existing columns in trade_records (parent_id, pnl_usd, etc.)
3. Run allocation computation batch job
4. Compare results: new allocations vs. old parent_id/pnl_usd
5. Reports use OLD system (no user-facing changes yet)

**Validation:**
```sql
-- Compare total PnL: old vs new
SELECT
    symbol,
    SUM(pnl_usd) as old_pnl
FROM trade_records
WHERE side = 'sell'
GROUP BY symbol;

SELECT
    symbol,
    SUM(pnl_usd) as new_pnl
FROM fifo_allocations
WHERE allocation_version = (SELECT MAX(allocation_version) FROM fifo_allocations)
GROUP BY symbol;
```

**Success Criteria:**
- Allocations computed for all symbols
- PnL discrepancies understood (expected for corrupted data)
- Performance acceptable (computation completes in reasonable time)

### Phase 2: Cutover (Week 3)

**Goal:** Switch reports to use new allocation system.

**Steps:**
1. Update reporting queries to use fifo_allocations
2. Create views that join trades with allocations
3. Deploy updated reports to staging
4. User acceptance testing
5. Deploy to production
6. Monitor for issues

**Rollback Plan:**
- Keep old columns intact
- Can switch reports back to old system if needed
- No data loss risk

### Phase 3: Cleanup (Week 4+)

**Goal:** Remove old columns, optimize schema.

**Steps:**
1. Mark old columns as deprecated (add comment)
2. Stop updating old columns (save compute)
3. Wait 1-2 weeks (ensure stability)
4. Drop old columns: `parent_id`, `parent_ids`, `pnl_usd`, etc.
5. Reclaim database space (VACUUM FULL)

**Before dropping columns:**
```sql
-- Backup just in case
CREATE TABLE trade_records_backup_old_pnl AS
SELECT order_id, parent_id, parent_ids, pnl_usd, cost_basis_usd
FROM trade_records
WHERE side = 'sell';
```

---

## API Design

### 1. Allocation Computation

```python
class FifoAllocationEngine:
    """
    Computes FIFO allocations for trade records.
    """

    async def compute_all_symbols(
        self,
        version: int = None,
        batch_id: UUID = None
    ) -> ComputationResult:
        """
        Compute allocations for all symbols.

        Args:
            version: Allocation version (auto-increment if None)
            batch_id: Batch ID for grouping (generate if None)

        Returns:
            ComputationResult with statistics and status
        """
        pass

    async def compute_symbol(
        self,
        symbol: str,
        version: int = None,
        batch_id: UUID = None
    ) -> ComputationResult:
        """
        Compute allocations for a single symbol.
        """
        pass

    async def compute_incremental(
        self,
        since: datetime,
        version: int = None
    ) -> ComputationResult:
        """
        Compute allocations only for trades since a timestamp.
        Uses inventory snapshots for efficiency.
        """
        pass


class AllocationValidator:
    """
    Validates allocation invariants.
    """

    async def validate_allocations(
        self,
        version: int,
        symbol: str = None
    ) -> ValidationResult:
        """
        Check that allocations satisfy invariants.

        Checks:
        - SUM(allocated_size) for each SELL equals sell.size
        - SUM(allocated_size) for each BUY <= buy.size
        - PnL calculations correct
        - No orphaned allocations (references non-existent trades)
        """
        pass

    async def find_discrepancies(
        self,
        version_a: int,
        version_b: int
    ) -> List[Discrepancy]:
        """
        Compare two allocation versions, report differences.
        """
        pass
```

### 2. CLI Commands

```bash
# Compute allocations
python -m scripts.compute_allocations --all-symbols
python -m scripts.compute_allocations --symbol BTC-USD
python -m scripts.compute_allocations --incremental --since "2025-11-20"

# Validate allocations
python -m scripts.validate_allocations --version 2
python -m scripts.validate_allocations --compare-versions 1 2

# Reports
python -m scripts.pnl_report --version 2 --period "last-30-days"
python -m scripts.pnl_report --version 2 --symbol BTC-USD
```

### 3. Scheduled Jobs

```python
# In main bot loop or separate cron job
async def scheduled_allocation_computation():
    """
    Run allocation computation periodically (e.g., hourly).
    """
    engine = FifoAllocationEngine(db_manager)

    # Incremental computation (fast)
    result = await engine.compute_incremental(
        since=datetime.now() - timedelta(hours=2)  # Overlap for safety
    )

    if result.status == 'completed':
        logger.info(f"Allocations computed: {result.allocations_created} allocations")
    else:
        logger.error(f"Allocation computation failed: {result.error_message}")
```

---

## Validation & Invariants

### Critical Invariants

These MUST hold for allocations to be correct:

```sql
-- 1. Each SELL is fully allocated (no missing or extra)
SELECT
    s.order_id,
    s.size as sell_size,
    COALESCE(SUM(a.allocated_size), 0) as allocated_total,
    s.size - COALESCE(SUM(a.allocated_size), 0) as discrepancy
FROM trade_records s
LEFT JOIN fifo_allocations a
    ON a.sell_order_id = s.order_id
    AND a.allocation_version = :version
WHERE s.side = 'sell'
GROUP BY s.order_id, s.size
HAVING ABS(s.size - COALESCE(SUM(a.allocated_size), 0)) > 0.00000001;
-- Should return 0 rows

-- 2. Each BUY is not over-allocated
SELECT
    b.order_id,
    b.size as buy_size,
    COALESCE(SUM(a.allocated_size), 0) as allocated_total,
    COALESCE(SUM(a.allocated_size), 0) - b.size as over_allocation
FROM trade_records b
LEFT JOIN fifo_allocations a
    ON a.buy_order_id = b.order_id
    AND a.allocation_version = :version
WHERE b.side = 'buy'
GROUP BY b.order_id, b.size
HAVING COALESCE(SUM(a.allocated_size), 0) > b.size + 0.00000001;
-- Should return 0 rows

-- 3. PnL calculation is correct
SELECT
    id,
    pnl_usd as stored_pnl,
    (net_proceeds_usd - cost_basis_usd) as computed_pnl,
    ABS(pnl_usd - (net_proceeds_usd - cost_basis_usd)) as discrepancy
FROM fifo_allocations
WHERE allocation_version = :version
  AND ABS(pnl_usd - (net_proceeds_usd - cost_basis_usd)) > 0.01;
-- Should return 0 rows

-- 4. No orphaned allocations
SELECT a.id, a.sell_order_id, a.buy_order_id
FROM fifo_allocations a
LEFT JOIN trade_records s ON s.order_id = a.sell_order_id
LEFT JOIN trade_records b ON b.order_id = a.buy_order_id
WHERE a.allocation_version = :version
  AND (s.order_id IS NULL OR b.order_id IS NULL);
-- Should return 0 rows

-- 5. Time ordering (can't sell before buy)
SELECT
    a.id,
    a.sell_order_id,
    a.buy_order_id,
    a.buy_time,
    a.sell_time
FROM fifo_allocations a
WHERE a.allocation_version = :version
  AND a.sell_time < a.buy_time;
-- Should return 0 rows
```

### Validation Views

Create views for easy monitoring:

```sql
-- View: Allocation health check
CREATE VIEW v_allocation_health AS
SELECT
    allocation_version,
    COUNT(*) as total_allocations,
    COUNT(DISTINCT sell_order_id) as sells_matched,
    COUNT(DISTINCT buy_order_id) as buys_used,
    SUM(pnl_usd) as total_pnl,
    MIN(allocation_time) as first_allocation,
    MAX(allocation_time) as last_allocation
FROM fifo_allocations
GROUP BY allocation_version;

-- View: Unmatched sells (shouldn't exist)
CREATE VIEW v_unmatched_sells AS
SELECT
    s.order_id,
    s.symbol,
    s.side,
    s.size,
    s.order_time,
    COALESCE(SUM(a.allocated_size), 0) as allocated
FROM trade_records s
LEFT JOIN fifo_allocations a ON a.sell_order_id = s.order_id
WHERE s.side = 'sell'
GROUP BY s.order_id, s.symbol, s.side, s.size, s.order_time
HAVING s.size - COALESCE(SUM(a.allocated_size), 0) > 0.00000001;

-- View: PnL by symbol (current version)
CREATE VIEW v_pnl_by_symbol AS
WITH latest_version AS (
    SELECT MAX(allocation_version) as version
    FROM fifo_allocations
)
SELECT
    a.symbol,
    COUNT(DISTINCT a.sell_order_id) as num_sells,
    SUM(a.pnl_usd) as total_pnl,
    AVG(a.pnl_usd) as avg_pnl_per_allocation,
    MIN(a.sell_time) as first_sell,
    MAX(a.sell_time) as last_sell
FROM fifo_allocations a
CROSS JOIN latest_version lv
WHERE a.allocation_version = lv.version
GROUP BY a.symbol
ORDER BY total_pnl DESC;
```

---

## Implementation Phases

### Week 1: Foundation

**Days 1-2: Database Schema**
- [ ] Create migration scripts
- [ ] Add new tables (fifo_allocations, fifo_computation_log, fifo_inventory_snapshot)
- [ ] Add indexes
- [ ] Create validation views
- [ ] Test migration on dev database

**Days 3-5: Core Engine**
- [ ] Implement `FifoAllocationEngine` class
- [ ] Implement allocation algorithm for single symbol
- [ ] Implement batch computation for all symbols
- [ ] Add logging and error handling
- [ ] Unit tests for algorithm

**Days 6-7: Validation**
- [ ] Implement `AllocationValidator` class
- [ ] Implement invariant checks
- [ ] Add discrepancy detection
- [ ] Integration tests

### Week 2: Integration & Testing

**Days 8-9: Trade Recorder Updates**
- [ ] Modify `trade_recorder.py` to NOT compute parent_id for new inserts
- [ ] Keep old columns for compatibility
- [ ] Update reconciliation logic (simplify)
- [ ] Test with live data (parallel mode)

**Days 10-11: Reporting Updates**
- [ ] Update PnL queries to use fifo_allocations
- [ ] Create new report views/functions
- [ ] Update dashboard queries
- [ ] Test report accuracy

**Days 12-14: Historical Recomputation**
- [ ] Run allocation computation on full historical data
- [ ] Validate results
- [ ] Compare with old system
- [ ] Document discrepancies
- [ ] Fix any bugs found

### Week 3: Deployment

**Days 15-17: Staging Deployment**
- [ ] Deploy to staging environment
- [ ] Run parallel operation (old + new systems)
- [ ] User acceptance testing
- [ ] Performance testing
- [ ] Fix any issues

**Days 18-20: Production Deployment**
- [ ] Deploy to production
- [ ] Monitor closely for first 24 hours
- [ ] Run validation checks hourly
- [ ] Be ready to rollback if needed

### Week 4+: Optimization & Cleanup

**Days 21+:**
- [ ] Optimize query performance
- [ ] Add incremental computation
- [ ] Add inventory snapshots
- [ ] Schedule automated allocation jobs
- [ ] Deprecate old columns
- [ ] Eventually drop old columns (after validation period)

---

## Risk Analysis

### High Risks

**1. Data Discrepancies**
- **Risk:** New allocations don't match expected PnL
- **Mitigation:** Extensive validation, parallel operation period
- **Rollback:** Keep old system active, can switch back

**2. Performance Issues**
- **Risk:** Allocation computation takes too long
- **Mitigation:** Process per-symbol (parallelizable), use incremental computation
- **Monitoring:** Track computation duration in fifo_computation_log

**3. Missing Edge Cases**
- **Risk:** Algorithm fails on unexpected data patterns
- **Mitigation:** Comprehensive testing, graceful error handling
- **Detection:** Validation queries catch invariant violations

### Medium Risks

**4. Migration Complexity**
- **Risk:** Schema changes cause downtime
- **Mitigation:** Additive changes only (new tables), no destructive changes initially
- **Testing:** Test migration on copy of production database

**5. Report Accuracy**
- **Risk:** Reports show different numbers, confuse users
- **Mitigation:** Document expected differences (fixing historical corruption)
- **Communication:** Explain to stakeholders why PnL changes

### Low Risks

**6. Code Bugs**
- **Risk:** Implementation bugs in allocation engine
- **Mitigation:** Unit tests, integration tests, code review
- **Detection:** Validation queries, user reports

**7. Incomplete Documentation**
- **Risk:** Future developers don't understand system
- **Mitigation:** This document, inline code comments, README
- **Maintenance:** Update docs with learnings

---

## Design Decisions (Resolved)

### 1. Versioning Strategy: Auto-Increment Globally ✅

**Decision:** Use auto-incrementing integer, globally across all symbols.

**Rationale:**
- Simple to implement and query
- Clear chronological ordering
- Easy to identify "latest" version (`MAX(allocation_version)`)
- Supports parallel operation (old vs new system)
- Enables audit trail and A/B testing

**Implementation:**
```python
def compute_allocations(symbol=None):
    # Get next version number
    next_version = db.execute(
        "SELECT COALESCE(MAX(allocation_version), 0) + 1 FROM fifo_allocations"
    ).scalar()

    # Compute with this version
    allocations = fifo_engine.compute(symbol, version=next_version)

    # Insert all allocations with same version
    db.bulk_insert(allocations)
```

**Use Cases:**
- **Parallel operation:** Version 1 (old system) vs Version 2 (new system)
- **Audit trail:** "What was PnL before bug fix?"
- **Algorithm comparison:** Test different FIFO variations

### 2. Incremental vs Full Recomputation ✅

**Decision:** Incremental by default, full when state invalid.

**Incremental Computation (Default):**

Use when:
- ✅ New fills arrive (normal trading)
- ✅ Database is immutable backwards in time
- ✅ FIFO algorithm unchanged
- ✅ Inventory snapshot exists and valid

```python
async def compute_incremental(self, since: datetime):
    """
    Only process fills after 'since' timestamp.
    Requires: Previous allocations + inventory state correct.
    """
    # 1. Load inventory snapshot from last computation
    inventory = load_inventory_snapshot(version=current_version)

    # 2. Fetch only NEW fills since last run
    new_buys = fetch_buys(order_time >= since)
    new_sells = fetch_sells(order_time >= since)

    # 3. Add new buys to inventory
    for buy in new_buys:
        inventory[buy.order_id] = buy.size

    # 4. Process new sells with existing inventory
    allocations = []
    for sell in new_sells:
        allocs = allocate_fifo(sell, inventory)
        allocations.extend(allocs)

    # 5. Save new allocations (same version, extending)
    db.insert(allocations)

    # 6. Update inventory snapshot
    save_inventory_snapshot(inventory, version=current_version)
```

**Full Recomputation (When Required):**

Triggers:
- ✅ Past trades amended/inserted (backfill)
- ✅ FIFO algorithm changed
- ✅ Bug discovered affecting history
- ✅ New report types need historical data
- ✅ Inventory snapshot suspected corrupt

```python
async def compute_full(self, new_version: int):
    """
    Recompute from scratch, ignore previous allocations.
    Creates NEW version number.
    """
    # 1. Start with empty inventory
    inventory = {}

    # 2. Fetch ALL fills chronologically
    all_buys = fetch_buys(order_by='order_time ASC')
    all_sells = fetch_sells(order_by='order_time ASC')

    # 3. Process everything from scratch
    allocations = compute_fifo(all_buys, all_sells, inventory)

    # 4. Insert with NEW version number
    db.bulk_insert(allocations, version=new_version)

    # 5. Save final inventory snapshot
    save_inventory_snapshot(inventory, version=new_version)
```

**Decision Logic:**
```python
def should_use_incremental(self) -> bool:
    """Decide whether incremental computation is safe."""

    # Check 1: Has algorithm changed?
    if algorithm_version_changed():
        return False  # Need full recompute

    # Check 2: Any historical amendments?
    latest_snapshot_time = get_latest_snapshot_time()
    has_old_trades = db.exists(
        """SELECT 1 FROM trade_records
           WHERE created_at > ? AND order_time < ?""",
        (latest_snapshot_time, latest_snapshot_time)
    )
    if has_old_trades:
        return False  # Historical insert detected

    # Check 3: Inventory snapshot exists and valid?
    if not inventory_snapshot_exists() or not validate_inventory():
        return False  # Snapshot corrupt or missing

    return True  # Safe to use incremental
```

### 3. Unmatched Sells: Log + Alert + Manual Review ✅

**Decision:** Detect, log, alert, and require manual investigation.

**Implementation:**
```python
async def handle_unmatched_sell(self, sell, unmatched_size, available_inventory):
    """
    Handle sell with no matching buy (shouldn't happen).
    """
    # 1. Log detailed warning
    self.logger.error(
        "UNMATCHED SELL DETECTED",
        extra={
            "sell_order_id": sell.order_id,
            "symbol": sell.symbol,
            "sell_size": float(sell.size),
            "unmatched_size": float(unmatched_size),
            "sell_time": sell.order_time,
            "available_inventory": {k: float(v) for k, v in available_inventory.items()}
        }
    )

    # 2. Send alert (email, Slack, etc.)
    await self.alert_manager.send_alert(
        severity="HIGH",
        title=f"Unmatched SELL: {sell.symbol}",
        message=f"Sell {sell.order_id} has {unmatched_size} unmatched. Manual investigation required."
    )

    # 3. Create placeholder allocation for visibility
    placeholder = FifoAllocation(
        sell_order_id=sell.order_id,
        buy_order_id=None,  # NULL indicates unmatched
        symbol=sell.symbol,
        allocated_size=unmatched_size,
        buy_price=None,
        sell_price=sell.price,
        pnl_usd=None,  # Can't compute without buy
        allocation_version=self.current_version,
        notes="UNMATCHED - Manual investigation required"
    )
    await self.db.insert(placeholder)

    # 4. Add to investigation queue
    await self.db.execute(
        """INSERT INTO manual_review_queue
           (order_id, issue_type, severity, created_at)
           VALUES (?, 'unmatched_sell', 'high', NOW())""",
        (sell.order_id,)
    )
```

**Detection in Reports:**
```sql
-- Flag unmatched sells
SELECT
    sell_order_id,
    symbol,
    allocated_size as unmatched_size,
    sell_time,
    'NEEDS INVESTIGATION' as status
FROM fifo_allocations
WHERE buy_order_id IS NULL;
```

**Future Enhancement:** As situations arise, build automated backfill logic for common scenarios.

### 4. Historical Corrupted Data: Start Fresh ✅

**Decision:** Clean start with Version 1, keep historical snapshot for reference.

**Rationale:**
- Fixing corruption is time-consuming and error-prone
- Starting fresh is cleaner and easier to validate
- Historical snapshot preserves forensic evidence if needed

**Implementation:**

**Step 1: Create Historical Snapshot**
```sql
-- Preserve old corrupted data for reference
CREATE TABLE historical_pnl_snapshot_20251120 AS
SELECT
    order_id,
    parent_id as old_parent_id,
    pnl_usd as old_pnl_usd,
    cost_basis_usd as old_cost_basis,
    'corrupted_historical' as note,
    NOW() as snapshot_time
FROM trade_records
WHERE side = 'sell';
```

**Step 2: Bootstrap Clean Version 1**
```python
async def bootstrap_allocations():
    """
    Fresh start - ignore all existing parent_id/pnl_usd values.
    """
    # Fetch ALL trades from trade_records
    # Completely ignore parent_id, parent_ids, pnl_usd columns
    # Run FIFO algorithm from scratch

    engine = FifoAllocationEngine(db_manager)
    result = await engine.compute_all_symbols(version=1)

    # Version 1 becomes the new source of truth
    logger.info(f"Bootstrap complete: {result.allocations_created} allocations created")
```

**Step 3: Communication**
```
Report Header (transitional period):
"PnL values updated Nov 20, 2025 - Historical data recomputed using corrected
FIFO algorithm. Previous reports may show different values due to calculation
errors in the old system."
```

**Tax Reporting Note:** Historical snapshot preserves what was originally reported for tax purposes, while Version 1 shows corrected values.

### 5. Granularity & Precision: Industry Best Practices ✅

**Decision:** Use arbitrary precision arithmetic with symbol-specific dust thresholds.

**Core Principles:**

1. **Fixed-Point Arithmetic (Never Float)**
```python
from decimal import Decimal

# Always use Decimal for financial calculations
size = Decimal('0.123456789012345678')  # Arbitrary precision
price = Decimal('45678.90')

# NEVER use float for money
size = 0.123456789012345678  # ❌ Loses precision
```

2. **Unlimited Precision Storage**
```sql
-- PostgreSQL NUMERIC is arbitrary precision
CREATE TABLE trade_records (
    size NUMERIC NOT NULL,      -- No precision limit
    price NUMERIC NOT NULL,
    total_fees_usd NUMERIC
);
```

3. **Symbol-Specific Configuration**
```python
SYMBOL_CONFIG = {
    'BTC-USD': {
        'base_precision': 8,
        'quote_precision': 2,
        'dust_threshold': Decimal('0.00001'),  # ~$0.50 at $50k
        'min_trade_size': Decimal('0.0001')
    },
    'ETH-USD': {
        'base_precision': 8,
        'quote_precision': 2,
        'dust_threshold': Decimal('0.0001'),
        'min_trade_size': Decimal('0.001')
    },
    'SHIB-USD': {
        'base_precision': 0,
        'quote_precision': 10,
        'dust_threshold': Decimal('1000'),     # 1000 SHIB minimum
        'min_trade_size': Decimal('10000')
    }
}
```

4. **Dust Handling in Allocation**
```python
def allocate_with_dust_handling(sell, buys, inventory):
    """
    FIFO allocation with dust threshold.
    """
    allocations = []
    remaining = sell.size
    dust_threshold = get_dust_threshold(sell.symbol)

    for buy in buys:
        available = inventory.get(buy.order_id, Decimal('0'))

        if available <= dust_threshold:
            continue  # Skip dust inventory

        allocated = min(available, remaining)

        if allocated > dust_threshold:
            allocation = create_allocation(sell, buy, allocated)
            allocations.append(allocation)
            inventory[buy.order_id] -= allocated
            remaining -= allocated

        if remaining <= dust_threshold:
            break  # Close enough - treat as fully allocated

    # Check remaining
    if remaining > dust_threshold:
        handle_unmatched_sell(sell, remaining, inventory)  # Truly unmatched
    elif remaining > Decimal('0'):
        logger.debug(f"Dust remaining for {sell.order_id}: {remaining}")  # Just dust

    return allocations
```

5. **Banker's Rounding (Fair)**
```python
from decimal import ROUND_HALF_EVEN

def round_for_symbol(value: Decimal, symbol: str, is_base: bool) -> Decimal:
    """
    Round value to appropriate precision for symbol.
    Uses banker's rounding to minimize bias.
    """
    base_prec, quote_prec = get_precision(symbol)
    decimals = base_prec if is_base else quote_prec

    quantizer = Decimal('1') / (Decimal('10') ** decimals)
    return value.quantize(quantizer, rounding=ROUND_HALF_EVEN)
```

6. **Validation with Dust Tolerance**
```sql
-- Check allocations sum correctly (with dust tolerance)
SELECT
    s.order_id,
    s.size as sell_size,
    COALESCE(SUM(a.allocated_size), 0) as allocated_total,
    s.size - COALESCE(SUM(a.allocated_size), 0) as discrepancy
FROM trade_records s
LEFT JOIN fifo_allocations a ON a.sell_order_id = s.order_id
WHERE s.side = 'sell'
GROUP BY s.order_id, s.size
HAVING ABS(s.size - COALESCE(SUM(a.allocated_size), 0)) > 0.00001  -- Dust threshold
ORDER BY discrepancy DESC;
```

**Configuration Module:** See `config/precision.py` for full implementation (to be created in Step C).

---

## Appendix: Example Data

### Example: Single SELL matched to multiple BUYs

**trade_records:**
```
BUY-1: BTC-USD, 0.5 BTC @ $30,000, time: 10:00
BUY-2: BTC-USD, 0.3 BTC @ $31,000, time: 10:05
SELL-1: BTC-USD, 0.7 BTC @ $32,000, time: 10:10
```

**fifo_allocations:**
```
Row 1: SELL-1 → BUY-1, allocated_size=0.5, cost_basis=$15,000, proceeds=$16,000, pnl=$1,000
Row 2: SELL-1 → BUY-2, allocated_size=0.2, cost_basis=$6,200, proceeds=$6,400, pnl=$200
```

**Validation:**
- SELL-1 size (0.7) = allocated_size sum (0.5 + 0.2) ✅
- BUY-1 used: 0.5 / 0.5 = 100% ✅
- BUY-2 used: 0.2 / 0.3 = 67% (0.1 remaining) ✅
- Total PnL: $1,000 + $200 = $1,200 ✅

---

## Version History

- **v1.0** (2025-11-20): Initial design document
- **v1.1** (2025-11-20): Resolved all open questions, finalized design decisions

---

## Approval

**Design Decisions Approved:**
- [x] Versioning Strategy: Auto-increment globally
- [x] Incremental/Full Computation Strategy
- [x] Unmatched Sells Handling
- [x] Historical Data Approach: Start fresh
- [x] Precision & Granularity: Industry best practices

**Ready for Implementation:** ✅

**Next Steps:**
1. Create precision configuration module (`config/precision.py`)
2. Create database migration scripts
3. Begin Phase 2: Core Implementation
