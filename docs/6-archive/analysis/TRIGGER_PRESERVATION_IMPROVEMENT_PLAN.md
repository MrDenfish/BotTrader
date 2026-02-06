# Trigger Preservation Improvement Plan

**Date:** 2026-01-04
**Purpose:** Outline for improving trigger metadata preservation from order placement through WebSocket recording
**Related:** `docs/ORDER_SIZING_DOCUMENTATION.md`, commits `c16877b`, `4c094a2`

---

## Executive Summary

Currently, strategy trigger information (like `rsi_oversold`, `ROC_MOMO`, `macd_cross`) is lost when orders are filled and recorded via WebSocket. The `trigger` field in `trade_records` ends up storing the order type ("limit") instead of the original strategy trigger.

This document outlines a comprehensive plan to preserve trigger metadata throughout the order lifecycle.

---

## Current Flow & Problem Analysis

### Current Order Lifecycle

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. SIGHOOK: Strategy Signal Generation                         │
├─────────────────────────────────────────────────────────────────┤
│  sighook/trading_strategy.py:decide_action()                   │
│    └─> Generates trigger: {"trigger": "rsi_oversold", ...}     │
│                                                                  │
│  sighook/order_manager.py:build_webhook_payload()              │
│    └─> Includes trigger in webhook payload                      │
└────────────┬────────────────────────────────────────────────────┘
             │ HTTP POST
             ↓
┌─────────────────────────────────────────────────────────────────┐
│ 2. WEBHOOK: Order Processing                                   │
├─────────────────────────────────────────────────────────────────┤
│  webhook/listener.py:handle_webhook()                          │
│    └─> Parses trigger from webhook JSON                        │
│                                                                  │
│  webhook/webhook_order_manager.py:place_order()                │
│    └─> OrderData contains trigger metadata                      │
│                                                                  │
│  webhook/webhook_order_types.py:place_limit_order()            │
│    ├─> Sends order to Coinbase                                  │
│    └─> ✅ CACHES trigger in order_triggers[order_id]           │
│       (webhook_order_manager.py:893-911)                        │
└────────────┬────────────────────────────────────────────────────┘
             │ Order placed to Coinbase
             ↓
┌─────────────────────────────────────────────────────────────────┐
│ 3. COINBASE: Order Execution                                   │
├─────────────────────────────────────────────────────────────────┤
│  Order sits on order book                                       │
│  Order gets filled by market                                    │
│  Fill event sent via WebSocket                                  │
└────────────┬────────────────────────────────────────────────────┘
             │ WebSocket fill event
             ↓
┌─────────────────────────────────────────────────────────────────┐
│ 4. WEBSOCKET: Fill Processing ⚠️ PROBLEM AREA                  │
├─────────────────────────────────────────────────────────────────┤
│  webhook/websocket_market_manager.py:process_user_channel()    │
│    └─> Receives fill event from Coinbase                        │
│                                                                  │
│  websocket_market_manager.py:_build_trade_from_fill()          │
│    ├─> Lines 335-336: Retrieves trigger from cache             │
│    │   trigger_cache = order_management.get("order_triggers")  │
│    │   cached_trigger = trigger_cache.get(base_id)             │
│    │                                                             │
│    ├─> ⚠️ PROBLEM 1: Cache lookup by base_id                   │
│    │   - Fill uses client_order_id (UUID)                       │
│    │   - Cache keyed by order_id from Coinbase response         │
│    │   - May not match if different ID formats used             │
│    │                                                             │
│    ├─> ⚠️ PROBLEM 2: Cache cleanup (line 342)                  │
│    │   - Cache entry deleted immediately after use               │
│    │   - If lookup fails first time, data is lost               │
│    │                                                             │
│    └─> ⚠️ PROBLEM 3: Fallback behavior (line 350)              │
│        - Falls back to: {"trigger": "websocket", ...}           │
│        - Original strategy trigger is lost                      │
│                                                                  │
│  webhook/listener.py:handle_websocket_message()                │
│    └─> Records trade to database with wrong trigger             │
│        Result: trigger = "limit" instead of "rsi_oversold"      │
└─────────────────────────────────────────────────────────────────┘
```

### Root Causes Identified

1. **ID Mismatch**: Cache uses `order_id` (from Coinbase API response), but WebSocket fills may use different ID field (`client_order_id` or parent order IDs)

2. **Premature Cache Cleanup**: Cache entry deleted immediately after first lookup, leaving no retry mechanism if initial lookup fails

3. **No Fallback to Database**: No mechanism to persist trigger metadata to database for later retrieval

4. **Multi-Order Complexity**: TP/SL orders create child orders, making ID mapping more complex

5. **Session Boundaries**: Orders placed before session restart lose trigger metadata (cache is in-memory)

---

## Proposed Solution: Multi-Layer Persistence

### Architecture Overview

Implement a **three-tier trigger preservation system**:

1. **Tier 1: In-Memory Cache** (existing, needs fixes)
2. **Tier 2: Database Persistence** (new)
3. **Tier 3: Inference Fallback** (existing in reports, keep as safety net)

---

## Implementation Plan

### Phase 1: Fix In-Memory Cache (Quick Win - 1-2 hours)

**Goal:** Fix immediate cache lookup issues

#### 1.1 Improve ID Matching

**File:** `webhook/websocket_market_manager.py:_build_trade_from_fill()`

**Current code (lines 335-350):**
```python
trigger_cache = (self.shared_data_manager.order_management or {}).get("order_triggers", {})
cached_trigger = trigger_cache.get(base_id)

if cached_trigger:
    trigger = cached_trigger
    # Clean up immediately
    trigger_cache.pop(base_id, None)
else:
    trigger = {"trigger": "websocket", "trigger_note": "trigger unknown"}
```

**Improved code:**
```python
trigger_cache = (self.shared_data_manager.order_management or {}).get("order_triggers", {})

# Try multiple ID formats for lookup
cached_trigger = None
for id_candidate in [base_id, fill_order_id, parent_id]:
    if id_candidate and id_candidate in trigger_cache:
        cached_trigger = trigger_cache[id_candidate]
        break

if cached_trigger:
    trigger = cached_trigger
    # Don't delete yet - delay cleanup for retry safety
    # Mark for cleanup instead
    trigger_cache[f"_cleanup_{base_id}"] = time.time()
else:
    # Try database lookup (Phase 2)
    trigger = await self._lookup_trigger_from_db(base_id) or \
              {"trigger": "websocket", "trigger_note": "trigger unknown"}
```

**Benefits:**
- Handles ID format variations
- Delays cleanup for retry safety
- Adds database fallback hook

---

#### 1.2 Implement Delayed Cache Cleanup

**File:** `webhook/websocket_market_manager.py`

**Add new method:**
```python
async def _cleanup_expired_trigger_cache(self):
    """
    Cleanup trigger cache entries older than 5 minutes.
    Called periodically to prevent memory growth.
    """
    try:
        order_mgmt = self.shared_data_manager.order_management or {}
        trigger_cache = order_mgmt.get("order_triggers", {})

        current_time = time.time()
        cleanup_age = 300  # 5 minutes

        # Find entries to clean
        to_delete = []
        for key in list(trigger_cache.keys()):
            if key.startswith("_cleanup_"):
                # Check if marked for cleanup and old enough
                original_key = key.replace("_cleanup_", "")
                if current_time - trigger_cache[key] > cleanup_age:
                    to_delete.append(original_key)
                    to_delete.append(key)

        # Remove old entries
        for key in to_delete:
            trigger_cache.pop(key, None)

        if to_delete:
            order_mgmt["order_triggers"] = trigger_cache
            await self.shared_data_manager.set_order_management(order_mgmt)
            self.logger.debug(f"Cleaned up {len(to_delete)//2} expired trigger cache entries")

    except Exception as e:
        self.logger.warning(f"Failed to cleanup trigger cache: {e}")
```

**Call from:** `webhook/listener.py:refresh_market_data()` (runs every 30s)

**Benefits:**
- Prevents immediate data loss
- Allows retry window for fills
- Automatic memory management

---

#### 1.3 Enhanced Logging

**File:** `webhook/websocket_market_manager.py:_build_trade_from_fill()`

**Add diagnostic logging:**
```python
# After cache lookup
if cached_trigger:
    self.logger.debug(
        f"✅ Retrieved trigger from cache: {cached_trigger.get('trigger')} "
        f"for order {base_id}"
    )
else:
    self.logger.warning(
        f"⚠️ Trigger cache miss for {symbol} order {base_id}. "
        f"Available cache keys: {list(trigger_cache.keys())[:5]}... "
        f"Tried: base_id={base_id}, fill_id={fill_order_id}, parent={parent_id}"
    )
```

**Benefits:**
- Diagnose cache lookup failures
- Identify ID mapping issues
- Monitor cache effectiveness

---

### Phase 2: Database Persistence (Medium-term - 4-6 hours)

**Goal:** Persist trigger metadata to survive session restarts

#### 2.1 Create Trigger Metadata Table

**File:** `TableModels/order_metadata.py` (new)

```python
from sqlalchemy import Column, String, JSON, DateTime, Index
from sqlalchemy.sql import func
from TableModels.base import Base

class OrderMetadata(Base):
    """
    Persistent storage for order metadata including trigger information.
    Survives session restarts and provides fallback for cache misses.
    """
    __tablename__ = "order_metadata"

    order_id = Column(String, primary_key=True)
    client_order_id = Column(String, index=True)  # For cross-reference
    symbol = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)
    trigger = Column(JSON, nullable=False)  # {"trigger": "rsi_oversold", ...}
    source = Column(String, nullable=False)  # "sighook", "webhook", etc.
    snapshot_id = Column(String, index=True)  # Strategy linkage
    score = Column(JSON)  # Buy/sell score metadata
    order_size_usd = Column(String)  # Notional size for verification
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    recorded_at = Column(DateTime(timezone=True))  # When trade was recorded

    __table_args__ = (
        Index('idx_order_metadata_symbol_created', 'symbol', 'created_at'),
        Index('idx_order_metadata_trigger', 'trigger', postgresql_using='gin'),
    )
```

**Migration:**
```sql
-- Run via alembic or manual migration
CREATE TABLE order_metadata (
    order_id VARCHAR PRIMARY KEY,
    client_order_id VARCHAR,
    symbol VARCHAR NOT NULL,
    side VARCHAR NOT NULL,
    trigger JSONB NOT NULL,
    source VARCHAR NOT NULL,
    snapshot_id VARCHAR,
    score JSONB,
    order_size_usd VARCHAR,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    recorded_at TIMESTAMPTZ
);

CREATE INDEX idx_order_metadata_client_order_id ON order_metadata(client_order_id);
CREATE INDEX idx_order_metadata_symbol_created ON order_metadata(symbol, created_at);
CREATE INDEX idx_order_metadata_trigger ON order_metadata USING GIN (trigger);
CREATE INDEX idx_order_metadata_snapshot ON order_metadata(snapshot_id);
```

---

#### 2.2 Persist Trigger on Order Placement

**File:** `webhook/webhook_order_manager.py:handle_order()`

**Add after successful order placement (around line 911):**

```python
# Cache trigger for later retrieval when websocket fill arrives
try:
    order_mgmt = self.shared_data_manager.order_management or {}
    trigger_cache = order_mgmt.get("order_triggers", {})
    trigger_cache[order_data.order_id] = order_data.trigger
    order_mgmt["order_triggers"] = trigger_cache

    await self.shared_data_manager.set_order_management(order_mgmt)
    self.logger.debug(f"Cached trigger for order {order_data.order_id}: {order_data.trigger}")

    # ✅ NEW: Persist to database for long-term storage
    await self._persist_order_metadata(order_data)

except Exception as e:
    self.logger.warning(f"Failed to cache/persist trigger for order {order_data.order_id}: {e}")
```

**Add new method:**
```python
async def _persist_order_metadata(self, order_data: OrderData):
    """
    Persist order metadata (including trigger) to database.
    Provides fallback for cache misses and survives session restarts.
    """
    try:
        from TableModels.order_metadata import OrderMetadata

        metadata = OrderMetadata(
            order_id=order_data.order_id,
            client_order_id=getattr(order_data, 'client_order_id', None),
            symbol=order_data.trading_pair,
            side=order_data.side,
            trigger=order_data.trigger,
            source=order_data.source,
            snapshot_id=getattr(order_data, 'snapshot_id', None),
            score=getattr(order_data, 'score', None),
            order_size_usd=str(order_data.order_amount_fiat) if order_data.order_amount_fiat else None,
            created_at=datetime.now(timezone.utc)
        )

        async with self.shared_data_manager.get_session() as session:
            session.add(metadata)
            await session.commit()

        self.logger.debug(f"Persisted order metadata for {order_data.order_id}")

    except Exception as e:
        self.logger.error(f"Failed to persist order metadata: {e}", exc_info=True)
        # Don't fail order placement on metadata persistence error
```

---

#### 2.3 Database Lookup on Cache Miss

**File:** `webhook/websocket_market_manager.py`

**Add new method:**
```python
async def _lookup_trigger_from_db(self, order_id: str) -> dict:
    """
    Lookup trigger metadata from database when cache miss occurs.
    Fallback for orders placed in previous sessions.
    """
    try:
        from TableModels.order_metadata import OrderMetadata
        from sqlalchemy import select

        async with self.shared_data_manager.get_session() as session:
            # Try exact order_id match
            result = await session.execute(
                select(OrderMetadata).where(OrderMetadata.order_id == order_id)
            )
            metadata = result.scalar_one_or_none()

            # Try client_order_id match if no exact match
            if not metadata:
                result = await session.execute(
                    select(OrderMetadata).where(OrderMetadata.client_order_id == order_id)
                )
                metadata = result.scalar_one_or_none()

            if metadata:
                # Update recorded_at timestamp
                metadata.recorded_at = datetime.now(timezone.utc)
                await session.commit()

                self.logger.info(
                    f"✅ Retrieved trigger from database: {metadata.trigger.get('trigger')} "
                    f"for order {order_id}"
                )
                return metadata.trigger

            return None

    except Exception as e:
        self.logger.error(f"Failed to lookup trigger from database: {e}", exc_info=True)
        return None
```

**Benefits:**
- Survives session restarts
- Provides audit trail
- Enables historical analysis

---

#### 2.4 Database Cleanup Strategy

**File:** `webhook/listener.py` or dedicated cleanup job

**Add periodic cleanup:**
```python
async def cleanup_old_order_metadata(self):
    """
    Cleanup order_metadata older than 30 days.
    Keep data retention manageable while preserving recent history.
    """
    try:
        from TableModels.order_metadata import OrderMetadata
        from sqlalchemy import delete

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=30)

        async with self.shared_data_manager.get_session() as session:
            result = await session.execute(
                delete(OrderMetadata).where(
                    OrderMetadata.created_at < cutoff_date
                )
            )
            await session.commit()

            deleted_count = result.rowcount
            if deleted_count > 0:
                self.logger.info(f"Cleaned up {deleted_count} old order metadata records")

    except Exception as e:
        self.logger.error(f"Failed to cleanup order metadata: {e}")
```

**Call from:** Daily report job or scheduled task

---

### Phase 3: Enhanced Reporting (Long-term - 2-3 hours)

**Goal:** Leverage preserved triggers for better insights

#### 3.1 Update Report Query to Use Real Triggers

**File:** `botreport/aws_daily_report.py:query_trigger_breakdown()`

**Enhanced query:**
```sql
WITH order_triggers AS (
    -- Get real trigger data from order_metadata if available
    SELECT
        om.order_id,
        COALESCE(
            om.trigger->>'trigger',
            'unknown'
        ) AS trigger_value,
        om.order_size_usd
    FROM order_metadata om
    WHERE om.created_at >= NOW() - INTERVAL '7 days'
),
buy_orders AS (
    SELECT
        tr.order_id,
        (tr.size * tr.price) AS notional_usd,
        CASE
            -- Use real trigger if available
            WHEN ot.trigger_value IS NOT NULL THEN
                CASE ot.trigger_value
                    WHEN 'rsi_oversold' THEN 'Signal: RSI Oversold'
                    WHEN 'rsi_overbought' THEN 'Signal: RSI Overbought'
                    WHEN 'macd_cross' THEN 'Signal: MACD Cross'
                    WHEN 'bb_squeeze' THEN 'Signal: BB Squeeze'
                    WHEN 'ROC_MOMO' THEN 'ROC Momentum'
                    WHEN 'ROC_MOMO_OVERRIDE' THEN 'ROC Momentum (Override)'
                    WHEN 'ROC' THEN 'ROC Momentum'
                    WHEN 'PASSIVE_BUY' THEN 'Passive MM'
                    ELSE 'Other: ' || ot.trigger_value
                END

            -- Fallback to size-based inference
            WHEN (tr.size * tr.price) BETWEEN 13 AND 17 THEN 'Signal Matrix (inferred)'
            WHEN (tr.size * tr.price) BETWEEN 18 AND 23 THEN 'ROC Momentum (inferred)'
            WHEN (tr.size * tr.price) BETWEEN 24 AND 30 THEN 'Webhook (inferred)'
            WHEN (tr.size * tr.price) BETWEEN 31 AND 34 THEN 'Passive MM (inferred)'
            ELSE 'Websocket'
        END AS inferred_trigger
    FROM trade_records tr
    LEFT JOIN order_triggers ot ON ot.order_id = tr.order_id
    WHERE tr.side = 'buy'
      AND tr.status IN ('filled', 'done')
)
-- ... rest of query ...
```

**Benefits:**
- Shows exact strategy triggers when available
- Distinguishes between real and inferred triggers
- Enables granular performance analysis (RSI vs MACD, etc.)

---

#### 3.2 Add Trigger Detail Section to Report

**File:** `botreport/aws_daily_report.py`

**Add new section after Trigger Breakdown:**

```python
# Detailed Trigger Breakdown (if real triggers available)
detailed_triggers = query_detailed_trigger_breakdown(conn)
if detailed_triggers:
    html_parts.append("""
        <h3>Detailed Trigger Analysis</h3>
        <p><i>Based on preserved trigger metadata</i></p>
        <table border="1" cellpadding="6" cellspacing="0">
          <tr>
            <th>Specific Trigger</th>
            <th>Count</th>
            <th>Win Rate</th>
            <th>Avg Win</th>
            <th>Avg Loss</th>
            <th>Total PnL</th>
          </tr>
    """)

    for trigger in detailed_triggers:
        html_parts.append(f"""
          <tr>
            <td>{trigger['trigger']}</td>
            <td>{trigger['count']}</td>
            <td>{trigger['win_rate']:.1f}%</td>
            <td>${trigger['avg_win']:.2f}</td>
            <td>${trigger['avg_loss']:.2f}</td>
            <td style="color:{'green' if trigger['total_pnl'] > 0 else 'red'}">${trigger['total_pnl']:.2f}</td>
          </tr>
        """)

    html_parts.append("</table>")
```

**Benefits:**
- Actionable insights per specific trigger
- Identify best-performing indicators
- Fine-tune strategy weights

---

## Testing Plan

### Unit Tests

**File:** `tests/test_trigger_preservation.py` (new)

```python
import pytest
from webhook.websocket_market_manager import WebSocketMarketManager
from TableModels.order_metadata import OrderMetadata

class TestTriggerPreservation:

    @pytest.mark.asyncio
    async def test_trigger_cache_lookup_multiple_ids(self):
        """Test cache lookup tries multiple ID formats"""
        # Setup
        mgr = WebSocketMarketManager(...)
        trigger_cache = {
            "coinbase-order-123": {"trigger": "rsi_oversold"}
        }

        # Test lookup with different ID
        result = await mgr._lookup_trigger_from_cache(
            base_id="client-uuid-456",
            fill_order_id="coinbase-order-123"
        )

        assert result == {"trigger": "rsi_oversold"}

    @pytest.mark.asyncio
    async def test_trigger_persists_to_database(self):
        """Test trigger metadata is saved to database"""
        # Place order with trigger
        order_data = OrderData(
            trigger={"trigger": "macd_cross"},
            order_id="test-123",
            ...
        )

        await order_manager._persist_order_metadata(order_data)

        # Verify in database
        async with session() as s:
            result = await s.execute(
                select(OrderMetadata).where(OrderMetadata.order_id == "test-123")
            )
            metadata = result.scalar_one()
            assert metadata.trigger == {"trigger": "macd_cross"}

    @pytest.mark.asyncio
    async def test_database_lookup_on_cache_miss(self):
        """Test fallback to database when cache misses"""
        # Setup: order in DB but not in cache
        await save_order_metadata(
            order_id="old-order-123",
            trigger={"trigger": "bb_squeeze"}
        )

        # Clear cache
        trigger_cache = {}

        # Lookup should find in database
        result = await mgr._build_trade_from_fill(fill_event)
        assert result["trigger"]["trigger"] == "bb_squeeze"
```

---

### Integration Tests

**Scenarios:**

1. **Happy Path**: Order placed → filled → trigger preserved
2. **Cache Miss**: Order from previous session → DB lookup succeeds
3. **ID Mismatch**: Different ID formats → still finds trigger
4. **TP/SL Orders**: Parent order trigger propagates to child orders
5. **Cache Cleanup**: Old entries removed without data loss

---

### Manual Testing Checklist

- [ ] Place order via sighook webhook
- [ ] Verify trigger cached in `order_triggers`
- [ ] Verify trigger persisted to `order_metadata` table
- [ ] Wait for fill via WebSocket
- [ ] Verify trigger preserved in `trade_records`
- [ ] Check email report shows correct trigger category
- [ ] Restart webhook container
- [ ] Verify old orders still have trigger info (DB lookup)
- [ ] Check detailed trigger report section

---

## Rollout Strategy

### Stage 1: Development & Testing (Week 1)

1. Implement Phase 1 (cache fixes)
2. Add unit tests
3. Test in development environment
4. Monitor logs for cache hit/miss rates

### Stage 2: Database Schema (Week 2)

1. Create `order_metadata` table
2. Run migration on staging database
3. Implement Phase 2 (DB persistence)
4. Test database lookup fallback

### Stage 3: Gradual Rollout (Week 3)

1. Deploy to AWS with feature flag
2. Monitor cache effectiveness
3. Monitor database growth
4. Verify trigger preservation rate

### Stage 4: Enhanced Reporting (Week 4)

1. Implement Phase 3 (detailed reports)
2. Verify report accuracy
3. Gather user feedback
4. Optimize based on findings

---

## Monitoring & Metrics

### Key Metrics to Track

1. **Trigger Cache Hit Rate**
   - Target: >95% for same-session orders
   - Alert if <80%

2. **Database Lookup Success Rate**
   - Target: >90% for cross-session orders
   - Alert if <70%

3. **Trigger Preservation Rate**
   - Target: >98% overall
   - Calculated: (orders with real triggers / total orders)

4. **Cache Memory Usage**
   - Monitor size of `order_triggers` cache
   - Alert if >10,000 entries (indicates cleanup failure)

5. **Database Growth**
   - Monitor `order_metadata` table size
   - Expected: ~500-1000 rows/day
   - Alert if >100MB (indicates cleanup not running)

### Logging Enhancements

**Add structured logs:**
```python
self.logger.info(
    "Trigger preservation metrics",
    extra={
        'cache_hit': True/False,
        'db_lookup': True/False,
        'trigger_found': True/False,
        'order_id': order_id,
        'trigger_type': trigger.get('trigger'),
        'latency_ms': lookup_time_ms
    }
)
```

---

## Rollback Plan

If issues arise:

1. **Phase 1 rollback**: Revert to single ID lookup (existing behavior)
2. **Phase 2 rollback**: Disable DB persistence, rely on cache + inference
3. **Phase 3 rollback**: Revert report to size-based inference

**Rollback triggers:**
- Trigger preservation rate <50%
- Database errors affecting order placement
- Performance degradation >200ms per order
- Memory leak in trigger cache

---

## Success Criteria

### Short-term (1 month)

- ✅ 95%+ cache hit rate for same-session orders
- ✅ 90%+ successful DB lookups for cross-session orders
- ✅ Email reports show real trigger types (not just "LIMIT")
- ✅ Zero order placement failures due to metadata persistence

### Medium-term (3 months)

- ✅ 98%+ overall trigger preservation rate
- ✅ Detailed trigger breakdown in all reports
- ✅ Ability to analyze performance by specific indicator (RSI vs MACD vs BB)
- ✅ Historical trigger data available for backtesting

### Long-term (6 months)

- ✅ Automated strategy optimization based on trigger performance
- ✅ Real-time trigger performance dashboard
- ✅ Trigger-based position sizing recommendations
- ✅ Complete audit trail for all trades

---

## Estimated Effort

| Phase | Complexity | Time | Risk |
|-------|-----------|------|------|
| Phase 1: Cache Fixes | Low | 2-3 hours | Low |
| Phase 2: DB Persistence | Medium | 4-6 hours | Medium |
| Phase 3: Enhanced Reports | Low | 2-3 hours | Low |
| Testing | Medium | 4-6 hours | Medium |
| **Total** | **Medium** | **12-18 hours** | **Medium** |

---

## Dependencies

- Database migration capability (alembic or manual)
- Access to modify shared_data_manager
- Access to modify websocket_market_manager
- Access to modify webhook_order_manager
- Testing environment with WebSocket connection

---

## Future Enhancements

1. **Real-time Trigger Dashboard**: Live monitoring of trigger performance
2. **Trigger-based Alerts**: Notify when specific triggers underperform
3. **A/B Testing**: Compare trigger variations
4. **ML-based Trigger Selection**: Train model on historical trigger performance
5. **Cross-Strategy Analysis**: Correlate triggers with market conditions

---

## References

- `docs/ORDER_SIZING_DOCUMENTATION.md` - Order sizing system
- `docs/ORDER_FLOW_DOCUMENTATION.md` - Complete order flow
- Commit `c16877b` - Order sizing fix
- Commit `4c094a2` - Trigger inference in reports
- `webhook/websocket_market_manager.py:333-350` - Current cache lookup
- `webhook/webhook_order_manager.py:893-911` - Current cache storage

---

**Document Version:** 1.0
**Last Updated:** 2026-01-04
**Author:** Claude Code
**Status:** Proposed - Awaiting Review
