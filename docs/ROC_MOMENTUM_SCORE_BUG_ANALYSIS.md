# ROC Momentum Score Bug: Complete Analysis and Fix

**Date**: December 30, 2025
**Status**: 🐛 **BUG IDENTIFIED - FIX REQUIRED**
**Impact**: ROC momentum orders have NULL scores, preventing trade-strategy linkage

---

## Executive Summary

ROC momentum orders are **intentionally** setting scores to NULL in `signal_manager.py`, which prevents the trade-strategy linkage system from working. This is a design decision (not a coding error), but it breaks the linkage feature for ROC trades.

**Root Cause**: Lines 364 and 375 in `sighook/signal_manager.py` explicitly set:
```python
'Score': {'Buy Score': None, 'Sell Score': None}
```

**Impact**:
- ROC momentum trades cannot be linked to strategy parameters
- Optimization analysis impossible for ROC signals
- Linkage rate artificially lowered for all sighook trades

---

## Complete ROC Order Flow Trace

### 1. Signal Detection (`signal_manager.py`)

**File**: `sighook/signal_manager.py`
**Function**: `get_signals()` (lines 340-376)

```python
# ROC priority overrides
roc_value = last_row.get('ROC', None)
roc_diff_value = last_row.get('ROC_Diff', 0.0)
rsi_value = last_row.get('RSI', None)

if roc_value is not None and rsi_value is not None:
    roc_thr_buy = float(self.roc_buy_threshold)  # e.g., +0.5% to +1.0%
    roc_thr_sell = float(self.roc_sell_threshold)  # e.g., -0.5% to -1.0%
    roc_diff_std = float(last_row.get('ROC_Diff_STD20', 0.3))
    accel_ok = abs(roc_diff_value) > max(0.3, 0.5 * roc_diff_std)

    buy_signal_roc = (roc_value > roc_thr_buy) and accel_ok and (rsi_value >= max(50.0, float(self.rsi_buy)))
    sell_signal_roc = (roc_value < roc_thr_sell) and accel_ok and (rsi_value <= min(50.0, float(self.rsi_sell)))

    if buy_signal_roc:
        # ✅ Computes scores for logging
        bs, ss, comps = self._compute_score_components(last_row)
        self._log_score_snapshot(symbol, ohlcv_df, bs, ss, comps, action='buy', trigger='roc_momo_override')

        # ❌ BUG: Returns NULL scores instead of computed scores
        return {
            'action': 'buy',
            'trigger': 'roc_momo',
            'type': 'limit',
            'Buy Signal': (1, float(roc_value), float(roc_thr_buy)),
            'Sell Signal': (0, None, None),
            'Score': {'Buy Score': None, 'Sell Score': None}  # ❌ Should use bs/ss
        }
```

**Code References**:
- **ROC detection logic**: `signal_manager.py:340-354`
- **Buy signal**: `signal_manager.py:355-365`
- **Sell signal**: `signal_manager.py:366-376`
- **❌ BUG location**: Lines 364, 375 (explicit NULL scores)

---

### 2. Strategy Order Building (`trading_strategy.py`)

**File**: `sighook/trading_strategy.py`
**Function**: `process_all_rows()` → `decide_action()` (lines 105-195)

```python
async def process_all_rows(self, ticker_cache, buy_sell_matrix, open_orders):
    strategy_results = []

    for index, row in ticker_cache.iterrows():
        asset = row['asset']
        symbol = row['symbol']
        ohlcv_df = await self.fetch_ohlcv_for_asset(asset)

        # Calls signal_manager to get action
        trade_decision = await self.decide_action(ohlcv_df, symbol)

        # Appends to results (includes action, trigger, score)
        strategy_results.append({'asset': asset, 'symbol': symbol, **trade_decision})

    return strategy_results, buy_sell_matrix
```

```python
async def decide_action(self, ohlcv_df: pd.DataFrame, symbol: str) -> Dict[str, Any]:
    # Calls signal_manager.get_signals()
    signals = self.signal_manager.get_signals(ohlcv_df, symbol)

    # signals = {
    #     'action': 'buy',
    #     'trigger': 'roc_momo',
    #     'type': 'limit',
    #     'Score': {'Buy Score': None, 'Sell Score': None}  # ❌ NULL from signal_manager
    # }

    return signals
```

**Note**: `trading_strategy.py` does NOT call `build_strategy_order()` for ROC momentum trades. The signal dict is returned as-is and passed directly to order_manager.

**Code References**:
- **Strategy processing**: `trading_strategy.py:105-166`
- **Action decision**: `trading_strategy.py:168-195`

---

### 3. Order Execution (`sender.py` → `order_manager.py`)

**File**: `sighook/sender.py`
**Function**: `run_bot()` (lines 384-406)

```python
# Part IV: Trading Strategies
strategy_results, buy_sell_matrix = await self.trading_strategy.process_all_rows(
    filtered_ticker_cache, buy_sell_matrix, open_orders
)

# Part V: Order Execution
submitted_orders = await self.order_manager.execute_actions(strategy_results, holdings_list)
```

**File**: `sighook/order_manager.py`
**Function**: `execute_actions()` → `handle_actions()` → `handle_buy_action()` (lines 387-530)

```python
async def execute_actions(self, strategy_orders, holdings):
    execution_tasks = []

    for order in strategy_orders:
        if order.get('action') not in ['buy', 'sell']:
            continue

        execution_tasks.append(self.handle_actions(order, holdings))

    return await asyncio.gather(*execution_tasks)
```

```python
async def handle_actions(self, order, holdings):
    # Delegates to handle_buy_action or handle_sell_action
    action_type = order.get('action')

    if action_type == 'buy':
        return await self.handle_buy_action(
            holdings, symbol, base_avail_to_trade, quote_avail_balance, price, order
        )
```

```python
async def handle_buy_action(self, holdings, symbol, base_avail_to_trade, quote_avail_balance, price, order):
    # ✅ Extracts score from order dict
    trigger = order.get("trigger", "score")
    score = order.get("score", {})  # {'Buy Score': None, 'Sell Score': None} ❌
    snapshot_id = order.get("snapshot_id")  # ✅ This exists

    # Builds webhook payload
    webhook_payload = self.build_webhook_payload(
        source='Matrix',
        symbol=symbol,
        side='buy',
        order_type='tp_sl',
        price=price,
        trigger=trigger,
        score=score,  # ❌ NULL scores passed through
        snapshot_id=snapshot_id,  # ✅ snapshot_id is correct
        ...
    )

    # Sends webhook
    await self.webhook.send_webhook(webhook_payload)
```

**Code References**:
- **Order execution**: `order_manager.py:387-417`
- **Action routing**: `order_manager.py:427-469`
- **Buy handling**: `order_manager.py:471-571`
- **Score extraction**: `order_manager.py:498-500`
- **Webhook building**: `order_manager.py:529-540, 710-771`
- **Score inclusion**: `order_manager.py:760`

---

### 4. Webhook Payload Transmission

**File**: `sighook/order_manager.py`
**Function**: `build_webhook_payload()` (lines 710-771)

```python
def build_webhook_payload(
    self, source, symbol, side, order_type, price, trigger,
    score: dict,  # {'Buy Score': None, 'Sell Score': None} ❌
    snapshot_id: str = None,  # ✅ Valid UUID
    ...
) -> dict:

    payload = {
        "timestamp": int(time.time() * 1000),
        "pair": symbol,
        "order_id": str(uuid.uuid4()),
        "action": side.lower(),
        "order_type": order_type,
        "side": side.lower(),
        "limit_price": price,
        "origin": "SIGHOOK",
        "source": source,
        "trigger": {"trigger": trigger},  # ✅ 'roc_momo'
        "score": score,  # ❌ {'Buy Score': None, 'Sell Score': None}
        "snapshot_id": snapshot_id,  # ✅ Valid UUID
        "verified": valid_order
    }

    return payload
```

**Actual Logged Payload** (from sighook logs):
```json
{
  "timestamp": 1767050529714,
  "pair": "AVNT-USD",
  "order_id": "2f200136-48c6-438c-855b-f4ef30e64cf8",
  "action": "buy",
  "order_type": "tp_sl",
  "order_amount_fiat": 20.0,
  "side": "buy",
  "limit_price": 0.4295,
  "origin": "SIGHOOK",
  "source": "Matrix",
  "trigger": {"trigger": "roc_momo"},
  "score": {"Buy Score": null, "Sell Score": null},  // ❌ NULL!
  "snapshot_id": "b042c0bb-6184-4d40-aa65-2f7c8d83a243",  // ✅ Valid
  "verified": "valid",
  "take_profit": 0.4402375,
  "stop_loss": 0.42091
}
```

**Code References**:
- **Payload construction**: `order_manager.py:745-771`
- **Score field**: `order_manager.py:760`

---

### 5. Webhook Reception and Metadata Caching

**File**: `webhook/listener.py`
**Function**: `_cache_strategy_metadata()` (lines 1106-1156)

```python
def _cache_strategy_metadata(self, trade_data: dict) -> None:
    product_id = trade_data.get("trading_pair")
    score = trade_data.get("score", {})  # {'Buy Score': None, 'Sell Score': None} ❌
    snapshot_id = trade_data.get("snapshot_id")  # ✅ Valid UUID

    # ❌ BUG: This check SHOULD fail, but doesn't because empty dict is truthy
    if not score and not snapshot_id:
        return  # Would skip caching

    # ✅ Cache is created (because snapshot_id exists)
    self.shared_data_manager.market_data['strategy_metadata_cache'][product_id] = {
        'score': score,  # ❌ {'Buy Score': None, 'Sell Score': None}
        'snapshot_id': snapshot_id,  # ✅ Valid
        'trigger': trigger,
        'side': side,
        'timestamp': timestamp
    }
```

**Code References**:
- **Caching logic**: `listener.py:1106-1156`
- **Validation check**: `listener.py:1133-1135`

---

### 6. Trade Recording and Linkage

**File**: `SharedDataManager/trade_recorder.py`
**Function**: `_create_or_update_strategy_link()` (lines 1184-1278)

```python
async def _create_or_update_strategy_link(self, session, order_id, symbol, side):
    # Retrieve cached metadata
    cache = self.shared_data_manager.market_data.get('strategy_metadata_cache', {})
    metadata = cache.get(symbol)

    if not metadata:
        self.logger.debug(f"No metadata cached for {symbol}, skipping linkage")
        return

    # Extract metadata
    score = metadata.get('score', {})  # {'Buy Score': None, 'Sell Score': None} ❌
    snapshot_id = metadata.get('snapshot_id')  # ✅ Valid UUID

    # ❌ This check PASSES (snapshot_id exists)
    if not snapshot_id:
        return

    # Extract scores
    buy_score = score.get('buy_score')  # ❌ None (key doesn't match 'Buy Score')

    # ✅ Linkage IS created with snapshot_id
    # ❌ But buy_score = None
    await self.create_strategy_link(
        order_id=order_id,
        snapshot_id=snapshot_id,  # ✅ Valid
        buy_score=buy_score,  # ❌ None
        sell_score=sell_score,  # ❌ None
        trigger_type=trigger,  # ✅ 'roc_momo'
        ...
    )
```

**Code References**:
- **Linkage creation**: `trade_recorder.py:1184-1278`
- **Metadata retrieval**: `trade_recorder.py:1204-1222`
- **Score extraction**: `trade_recorder.py:1224-1247`

---

## The Bug: Detailed Breakdown

### Location:
**File**: `sighook/signal_manager.py`
**Lines**: 364, 375

### Current Code (BUGGY):
```python
if buy_signal_roc:
    # Computes scores for logging
    bs, ss, comps = self._compute_score_components(last_row)
    self._log_score_snapshot(symbol, ohlcv_df, bs, ss, comps, action='buy', trigger='roc_momo_override')

    # ❌ Returns NULL scores instead of using computed bs/ss
    return {
        'action': 'buy',
        'trigger': 'roc_momo',
        'type': 'limit',
        'Buy Signal': (1, float(roc_value), float(roc_thr_buy)),
        'Sell Signal': (0, None, None),
        'Score': {'Buy Score': None, 'Sell Score': None}  # ❌ BUG HERE
    }
```

### Why This Is Wrong:

1. **Scores ARE computed** at line 357: `bs, ss, comps = self._compute_score_components(last_row)`
2. **Scores ARE logged** at line 358: `self._log_score_snapshot(..., bs, ss, ...)`
3. **Scores are discarded** at line 364: `'Score': {'Buy Score': None, 'Sell Score': None}`

This suggests the NULL scores were **intentional** at some point (perhaps to differentiate ROC trades from indicator-weighted trades), but this breaks the linkage system.

---

## The Fix

### Option A: Use Computed Scores (RECOMMENDED)

**Change lines 364 and 375** to use the computed scores:

```python
if buy_signal_roc:
    bs, ss, comps = self._compute_score_components(last_row)
    self._log_score_snapshot(symbol, ohlcv_df, bs, ss, comps, action='buy', trigger='roc_momo_override')

    return {
        'action': 'buy',
        'trigger': 'roc_momo',
        'type': 'limit',
        'Buy Signal': (1, float(roc_value), float(roc_thr_buy)),
        'Sell Signal': (0, None, None),
        'Score': {'Buy Score': bs, 'Sell Score': ss}  # ✅ FIX: Use computed scores
    }

if sell_signal_roc:
    bs, ss, comps = self._compute_score_components(last_row)
    self._log_score_snapshot(symbol, ohlcv_df, bs, ss, comps, action='sell', trigger='roc_momo_override')

    return {
        'action': 'sell',
        'trigger': 'roc_momo',
        'type': 'limit',
        'Sell Signal': (1, float(roc_value), float(roc_thr_sell)),
        'Buy Signal': (0, None, None),
        'Score': {'Buy Score': bs, 'Sell Score': ss}  # ✅ FIX: Use computed scores
    }
```

**Benefits**:
- ✅ ROC trades get full indicator scores
- ✅ Linkage system works
- ✅ Can analyze which indicators contributed to ROC signals
- ✅ Consistent with other strategy trades

**Drawbacks**:
- None (scores are already being computed, just not used)

---

### Option B: Use ROC Value as Score

**Alternative**: Use the ROC value itself as the score:

```python
if buy_signal_roc:
    bs, ss, comps = self._compute_score_components(last_row)
    self._log_score_snapshot(symbol, ohlcv_df, bs, ss, comps, action='buy', trigger='roc_momo_override')

    return {
        'action': 'buy',
        'trigger': 'roc_momo',
        'type': 'limit',
        'Buy Signal': (1, float(roc_value), float(roc_thr_buy)),
        'Sell Signal': (0, None, None),
        'Score': {'Buy Score': float(roc_value), 'Sell Score': None}  # ✅ Use ROC value
    }
```

**Benefits**:
- ✅ Linkage system works
- ✅ Easier to identify ROC trades (score = ROC %)
- ✅ More semantically accurate (score represents ROC momentum)

**Drawbacks**:
- ⚠️ Loses indicator breakdown
- ⚠️ Different score semantics than other trades

---

### Option C: Add ROC-Specific Metadata Field

**Most comprehensive**: Keep existing scores AND add ROC-specific field:

```python
if buy_signal_roc:
    bs, ss, comps = self._compute_score_components(last_row)
    self._log_score_snapshot(symbol, ohlcv_df, bs, ss, comps, action='buy', trigger='roc_momo_override')

    return {
        'action': 'buy',
        'trigger': 'roc_momo',
        'type': 'limit',
        'Buy Signal': (1, float(roc_value), float(roc_thr_buy)),
        'Sell Signal': (0, None, None),
        'Score': {
            'Buy Score': bs,  # ✅ Full indicator score
            'Sell Score': ss,
            'ROC_Value': float(roc_value),  # ✅ Additional ROC-specific data
            'ROC_Threshold': float(roc_thr_buy),
            'ROC_Accel': float(roc_diff_value)
        }
    }
```

**Benefits**:
- ✅ Best of both worlds
- ✅ Full indicator scores + ROC details
- ✅ Maximum data for optimization

**Drawbacks**:
- ⚠️ Requires schema changes (trade_strategy_link table needs JSON field for extended metadata)
- ⚠️ More complex

---

## Recommended Action

### Immediate Fix (Deploy Today):

**File**: `sighook/signal_manager.py`
**Lines**: 364, 375

```python
# Line 364 - BUY signal
'Score': {'Buy Score': bs, 'Sell Score': ss}

# Line 375 - SELL signal
'Score': {'Buy Score': bs, 'Sell Score': ss}
```

### Verification Steps:

1. Make the code change
2. Commit and push to `feature/strategy-optimization`
3. Deploy to AWS (rebuild sighook container)
4. Wait for next ROC signal
5. Check sighook logs - score should NOT be NULL
6. Check webhook logs - metadata should be cached
7. Check database - linkage should be created with scores

### Testing:

```python
# Test that scores are populated
def test_roc_momentum_signal_has_scores():
    signal_manager = SignalManager(...)
    ohlcv_df = create_test_df_with_roc_signal()

    result = signal_manager.get_signals(ohlcv_df, 'BTC-USD')

    assert result['action'] == 'buy'
    assert result['trigger'] == 'roc_momo'
    assert result['Score']['Buy Score'] is not None  # ✅ Should have score
    assert isinstance(result['Score']['Buy Score'], (int, float))
```

---

## Additional Findings

### Secondary Issue: Score Key Mismatch

**File**: `SharedDataManager/trade_recorder.py`
**Line**: 1232

```python
buy_score = score.get('buy_score')  # ❌ Wrong key - should be 'Buy Score'
```

The cache stores `{'Buy Score': 75.3}` (capital B, capital S, with space), but linkage code looks for `'buy_score'` (lowercase, underscore).

**Fix**:
```python
buy_score = score.get('Buy Score')  # ✅ Match webhook payload format
```

OR normalize keys when caching:
```python
# In listener.py:_cache_strategy_metadata()
score_normalized = {
    'buy_score': score.get('Buy Score'),
    'sell_score': score.get('Sell Score')
}
```

---

## Impact Analysis

### Current State:
- ❌ ROC momentum trades have NULL scores
- ❌ Linkage created but with buy_score=NULL, sell_score=NULL
- ❌ Cannot analyze ROC trade performance via linkage data
- ❌ Linkage rate artificially low

### After Fix:
- ✅ ROC momentum trades have full indicator scores
- ✅ Linkage works correctly
- ✅ Can optimize ROC strategy parameters
- ✅ Accurate linkage rate metrics

---

## Timeline

**Estimated Effort**: 15 minutes
- 5 min: Code change (2 lines)
- 5 min: Testing
- 5 min: Deploy + verify

**Priority**: P1 (Blocks optimization analysis for ROC trades)

---

**Status**: Ready for implementation
**Assigned**: Development Team
**Blocked By**: None
