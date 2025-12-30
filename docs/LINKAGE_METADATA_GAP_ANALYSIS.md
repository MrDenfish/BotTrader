# Trade-Strategy Linkage: Critical Metadata Correlation Gap

**Date**: December 30, 2025
**Status**: 🚨 **CRITICAL ISSUE IDENTIFIED**
**Impact**: Linkage system cannot reliably correlate WebSocket fill events with originating order metadata

---

## Problem Summary

The trade-strategy linkage system has a **fundamental architectural flaw** in how it correlates metadata between order placement and order fills:

1. **Sighook** generates metadata (snapshot_id, scores) when creating an order
2. **Webhook** caches this metadata **keyed by product_id** (e.g., "BTC-USD")
3. **WebSocket fill event** arrives with only: `order_id`, `product_id`, `price`, `size`, `status`
4. **Trade recorder** retrieves cached metadata **by product_id**
5. **❌ If multiple orders exist for same symbol, wrong metadata is used**

---

## Current Architecture (FLAWED)

### Order Placement Flow

```
SIGHOOK:
  order_manager.py:handle_buy_action()
    ├── order = {
    │     'order_id': None,  # ❌ Not generated yet (Coinbase assigns this)
    │     'pair': 'BTC-USD',
    │     'snapshot_id': 'abc-123',
    │     'score': {'buy_score': 75.3}
    │   }
    └── send_webhook(payload)

WEBHOOK:
  listener.py:_cache_strategy_metadata()
    └── cache['BTC-USD'] = {  # ❌ Keyed by SYMBOL, not ORDER_ID
          'snapshot_id': 'abc-123',
          'score': {'buy_score': 75.3}
        }
```

### Order Fill Flow (Hours/Days Later)

```
COINBASE EXCHANGE:
  → WebSocket "match" event:
      {
        'order_id': 'coinbase-generated-id-12345',  # ✅ Now we have this
        'product_id': 'BTC-USD',                    # ✅ We have this
        'filled_size': 0.001,
        'price': 50000,
        'status': 'FILLED'
        # ❌ NO snapshot_id
        # ❌ NO score
        # ❌ NO trigger
      }

WEBHOOK:
  trade_recorder.py:_create_or_update_strategy_link()
    ├── symbol = 'BTC-USD'
    ├── metadata = cache.get('BTC-USD')  # ❌ Gets MOST RECENT, not SPECIFIC order
    └── If cache empty or wrong entry → linkage fails
```

---

## Race Conditions & Failure Scenarios

### Scenario 1: Multiple Orders for Same Symbol

```
T+0s:  BUY BTC-USD #1 placed (snapshot_id='aaa')
       cache['BTC-USD'] = {snapshot_id: 'aaa', score: 75.3}

T+5s:  BUY BTC-USD #2 placed (snapshot_id='bbb')
       cache['BTC-USD'] = {snapshot_id: 'bbb', score: 82.1}  # ← OVERWRITES!

T+10s: Order #1 fills
       → trade_recorder gets cache['BTC-USD']
       → Links order #1 to snapshot_id='bbb' ❌ WRONG!

T+15s: Order #2 fills
       → cache['BTC-USD'] was cleared after order #1
       → No metadata found ❌ NO LINKAGE!
```

### Scenario 2: Delayed Fill (Cache Expired)

```
T+0s:  BUY BTC-USD placed
       cache['BTC-USD'] = {snapshot_id: 'aaa', score: 75.3}

T+1s:  Order fills
       → trade_recorder retrieves cache
       → Creates linkage ✅
       → Clears cache['BTC-USD']

T+5m:  Partial fill arrives (delayed WebSocket event)
       → cache['BTC-USD'] is empty
       → No linkage for partial fill ❌
```

### Scenario 3: Sell Order Overwrites Buy Cache

```
T+0s:  BUY BTC-USD placed
       cache['BTC-USD'] = {snapshot_id: 'aaa', side: 'buy', score: {'buy_score': 75.3}}

T+10s: BUY fills successfully, linkage created, cache cleared ✅

T+1h:  Position monitor triggers SELL for BTC-USD
       ❌ NO metadata cached (exits don't have snapshot_id)

T+1h:  SELL fills
       → No metadata in cache
       → Linkage UPDATE fails (no sell_score recorded)
```

### Scenario 4: WebSocket Disconnect/Reconnect

```
T+0s:  BUY order placed
       cache['BTC-USD'] = {metadata}

T+30s: WebSocket disconnects

T+1m:  Order fills (event missed during disconnect)

T+2m:  WebSocket reconnects
       → Reconciliation detects filled order
       → No metadata in cache (30s+ old)
       → No linkage ❌
```

---

## Why Current Design Fails

### Problem 1: Cache Key Mismatch

**Cache is keyed by**: `product_id` (symbol)
**Fills are identified by**: `order_id`
**No correlation mechanism** between the two

### Problem 2: Order ID Not Known at Placement Time

When sighook places an order:
- It sends metadata to webhook ✅
- **Coinbase hasn't assigned order_id yet** ❌
- Webhook caches metadata **before** knowing the order_id
- When fill arrives, we have order_id but can't match it to cached entry

### Problem 3: One-to-Many Relationship

- **ONE cache entry per symbol** (product_id)
- **MANY orders can exist for same symbol**
- Last cache write wins, previous metadata lost

### Problem 4: Cache Cleared Too Early

- Cache cleared immediately after linkage creation
- Partial fills that arrive later have no metadata
- Multi-leg orders (split fills) only get metadata for first fill

---

## Evidence from Logs

From deployment on 2025-12-29 21:00 UTC:

### Sighook Logs Show Metadata Being Sent:
```json
{
  "snapshot_id": "b042c0bb-6184-4d40-aa65-2f7c8d83a243",
  "score": {"Buy Score": null, "Sell Score": null},
  "trigger": "roc_momo",
  "pair": "AVNT-USD"
}
```

### Database Shows 0% Linkage:
```sql
SELECT COUNT(*) AS total_trades,
       COUNT(tsl.order_id) AS linked_trades
FROM trade_records tr
LEFT JOIN trade_strategy_link tsl ON tr.order_id = tsl.order_id
WHERE tr.order_time >= '2025-12-29 21:00:00+00';

-- Result: 3 total, 0 linked (0%)
```

### Why Zero Linkage?

All 3 trades were `source='websocket'` (manual/external trades), not from sighook.
But even if sighook HAD executed trades, the current cache design would fail for the scenarios above.

---

## Required Architecture Changes

### Option A: Persist Metadata at Order Placement (RECOMMENDED)

**When order is placed:**
1. Sighook sends webhook with metadata
2. Webhook places order via Coinbase API
3. **Coinbase returns order_id in API response**
4. **IMMEDIATELY write to database:**
   ```sql
   INSERT INTO order_metadata_staging (
     order_id,  -- ✅ Now we have this from Coinbase response
     snapshot_id,
     score,
     trigger,
     side,
     timestamp
   ) VALUES (...);
   ```
5. When fill arrives, query `order_metadata_staging` by `order_id`

**Benefits:**
- ✅ Survives restarts (persistent)
- ✅ No race conditions (one entry per order_id)
- ✅ Works for partial fills (not cleared after first fill)
- ✅ Handles WebSocket disconnects (data in DB)

**Downsides:**
- Requires new database table
- Slight latency increase (DB write)

---

### Option B: Cache by order_id After Placement

**Flow:**
1. Sighook sends metadata to webhook
2. Webhook caches temporarily by product_id
3. Webhook places order, gets order_id from Coinbase
4. **Re-cache by order_id:**
   ```python
   # After Coinbase API response
   order_id = response['order_id']
   cache[order_id] = metadata  # ✅ Now keyed by order_id
   del cache[product_id]       # ✅ Remove product_id key
   ```
5. When fill arrives, use `cache[order_id]`

**Benefits:**
- ✅ No new database table
- ✅ Correct correlation

**Downsides:**
- ❌ Doesn't survive restarts
- ❌ Lost if webhook container restarts between placement and fill
- ❌ Still fails on WebSocket disconnect/reconnect

---

### Option C: Embed Metadata in client_order_id

**Flow:**
1. When placing order, embed snapshot_id in `client_order_id`:
   ```python
   client_order_id = f"sighook-{snapshot_id[:8]}-{timestamp}"
   ```
2. Store full mapping in memory:
   ```python
   metadata_by_coid[client_order_id] = {
     'snapshot_id': full_snapshot_id,
     'score': score,
     'trigger': trigger
   }
   ```
3. When fill arrives, extract from `client_order_id`
4. Lookup full metadata

**Benefits:**
- ✅ Client order ID travels with the order
- ✅ No additional database writes

**Downsides:**
- ❌ client_order_id is limited length (36 chars on Coinbase)
- ❌ Still in-memory cache (lost on restart)
- ❌ Fragile parsing logic

---

### Option D: Query Coinbase for Order Details on Fill

**Flow:**
1. When fill arrives with order_id
2. Query Coinbase REST API: `GET /orders/{order_id}`
3. Extract `client_order_id` from response
4. Parse client_order_id to extract snapshot_id
5. Query database for full metadata

**Benefits:**
- ✅ No caching needed
- ✅ Works after restarts

**Downsides:**
- ❌ API rate limits
- ❌ Latency (network roundtrip per fill)
- ❌ Fails if Coinbase API down

---

## Recommended Solution: Hybrid Approach

**Combine Option A + Option B:**

### Phase 1: Immediate (In-Memory Cache by order_id)
```python
# webhook_order_manager.py:attempt_order_placement()

# After order placed successfully
response = await coinbase_api.place_order(...)
order_id = response['order_id']

# Re-key cache by order_id
metadata = self.shared_data_manager.market_data['strategy_metadata_cache'].pop(product_id, None)
if metadata:
    # NEW: Also cache by order_id
    if 'strategy_metadata_by_order_id' not in self.shared_data_manager.market_data:
        self.shared_data_manager.market_data['strategy_metadata_by_order_id'] = {}

    self.shared_data_manager.market_data['strategy_metadata_by_order_id'][order_id] = metadata
    self.logger.info(f"[STRATEGY_CACHE] Re-cached by order_id: {order_id}")
```

```python
# trade_recorder.py:_create_or_update_strategy_link()

# Try order_id cache first, fall back to product_id
cache_by_order = self.shared_data_manager.market_data.get('strategy_metadata_by_order_id', {})
cache_by_product = self.shared_data_manager.market_data.get('strategy_metadata_cache', {})

metadata = cache_by_order.get(order_id) or cache_by_product.get(symbol)
```

### Phase 2: Persistent (Database Table)
```sql
CREATE TABLE order_metadata_staging (
    order_id VARCHAR(255) PRIMARY KEY,
    snapshot_id UUID NOT NULL,
    buy_score NUMERIC,
    sell_score NUMERIC,
    trigger_type VARCHAR(50),
    indicator_breakdown JSONB,
    indicators_fired INTEGER,
    side VARCHAR(10),
    product_id VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW(),
    consumed_at TIMESTAMP,  -- When linkage was created

    INDEX idx_product_id (product_id),
    INDEX idx_created_at (created_at),
    INDEX idx_consumed_at (consumed_at)
);
```

**Cleanup Strategy:**
- DELETE WHERE consumed_at < NOW() - INTERVAL '24 hours'
- Prevents table growth
- Keeps recent for debugging

---

## Immediate Action Items

### Critical (Deploy ASAP):
1. **Add order_id cache** after order placement (Option B)
2. **Test with next sighook trade** to verify linkage works
3. **Add logging** to track cache hits/misses

### Important (Next Sprint):
1. **Create order_metadata_staging table** (Option A)
2. **Migrate to persistent storage** for reliability
3. **Add metrics** for linkage success rate by cache type

### Monitoring:
1. **Track cache misses** - log when fill arrives but no metadata
2. **Alert on 0% linkage** - if >10 sighook trades with 0% linkage
3. **Dashboard** showing:
   - Cache hit rate (order_id vs product_id vs miss)
   - Linkage rate by order source
   - Average time between placement and fill

---

## Testing Recommendations

### Unit Tests:
```python
def test_metadata_cache_by_order_id():
    # Place order, cache by product_id
    # Get order_id from response
    # Re-cache by order_id
    # Simulate fill event
    # Verify correct metadata retrieved

def test_multiple_orders_same_symbol():
    # Place 2 BTC-USD orders with different snapshot_ids
    # Ensure each fill gets correct metadata
```

### Integration Tests:
```python
async def test_webhook_disconnect_during_fill():
    # Place order
    # Disconnect WebSocket
    # Order fills
    # Reconnect
    # Verify linkage created via reconciliation
```

---

## Conclusion

The current linkage implementation has a **fundamental design flaw** where:
- Metadata is cached by `product_id` (symbol)
- Fills are identified by `order_id`
- No reliable correlation mechanism exists

This explains:
- ✅ Why 0% linkage rate is observed (trades are manual, not sighook)
- ⚠️ But even when sighook trades execute, linkage would fail due to cache design
- 🚨 Multiple orders for same symbol would get incorrect metadata

**Immediate fix**: Re-cache by `order_id` after placement (can deploy today)
**Long-term solution**: Persistent `order_metadata_staging` table (implement next sprint)

---

**Status**: Awaiting approval for implementation
**Assigned**: Development Team
**Priority**: P0 (Blocks parameter optimization)
