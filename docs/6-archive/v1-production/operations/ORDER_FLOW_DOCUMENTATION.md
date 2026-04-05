# BotTrader Order Flow Documentation

**Date**: December 30, 2025
**Purpose**: Comprehensive guide to all order creation paths and their metadata flows
**Related**: [ARCHITECTURE_DEEP_DIVE.md](../architecture/ARCHITECTURE_DEEP_DIVE.md), [LINKAGE_INTEGRATION_DEPLOYMENT.md](../../archive/sessions/LINKAGE_INTEGRATION_DEPLOYMENT.md)

---

## Table of Contents

1. [Overview](#overview)
2. [Order Source Types](#order-source-types)
3. [Sighook-Originated Orders](#sighook-originated-orders)
4. [Passive Market Making Orders](#passive-market-making-orders)
5. [Position Monitor Exit Orders](#position-monitor-exit-orders)
6. [Manual/External Orders](#manualexternal-orders)
7. [ROC Momentum Orders](#roc-momentum-orders)
8. [Order Metadata & Linkage](#order-metadata--linkage)
9. [Order Flow Diagrams](#order-flow-diagrams)

---

## Overview

The BotTrader system creates orders through **5 distinct entry points**, each with different characteristics, metadata availability, and linkage potential:

| Entry Point | Source Label | Has Strategy Metadata | Linkage-Capable | Execution Mode |
|-------------|--------------|----------------------|-----------------|----------------|
| Sighook Strategy | `sighook` | ✅ Yes | ✅ Yes | Webhook → Coinbase |
| Passive Market Making | `sighook` | ⚠️ Partial | 🔶 Possible | Direct → Coinbase |
| Position Monitor Exits | `webhook` | ❌ No | ❌ No | Webhook → Coinbase |
| ROC Momentum | `sighook` | ✅ Yes | ✅ Yes | Webhook → Coinbase |
| Manual/External | `websocket` | ❌ No | ❌ No | External → Detected |

---

## Order Source Types

### Database `source` Field Values

The `trade_records.source` field identifies how an order entered the system:

- **`sighook`**: Order originated from sighook container (strategy or passive MM)
- **`webhook`**: Order originated from webhook container (position monitor exits)
- **`websocket`**: Order detected via WebSocket feed (manual trades, Advanced Trade app)
- **`reconciled`**: Backfilled from REST API reconciliation
- **`manual`**: Explicitly marked as manual trade

---

## Sighook-Originated Orders

### 1. Strategy-Based Trades (Indicator Signals)

**Entry Point**: `sighook/trading_strategy.py` → `sighook/order_manager.py`

#### Flow Path:
```
sender.py:run_bot() (every 5 minutes)
    ↓
trading_strategy.py:build_strategy_order()
    ├── Evaluates technical indicators
    ├── Calculates buy_score / sell_score
    ├── Generates snapshot_id (UUID per bot run)
    └── Returns strategy_order dict
    ↓
order_manager.py:handle_buy_action() / handle_sell_action()
    ├── Extracts snapshot_id, score from order dict
    ├── Builds webhook payload with metadata
    └── Calls alerts_msgs_webhooks.py:send_webhook()
    ↓
webhook/listener.py:handle_webhook_request()
    ├── Parses webhook payload
    ├── Caches metadata (snapshot_id, score, trigger)
    └── Places order via webhook_order_manager
    ↓
COINBASE EXCHANGE
    ↓ (WebSocket fill event)
    ↓
SharedDataManager/trade_recorder.py:record_trade()
    ├── Retrieves cached metadata
    ├── Calls create_strategy_link()
    └── Links trade to strategy parameters
```

#### Metadata Captured:
- ✅ **snapshot_id** - Unique ID per bot run (UUID)
- ✅ **buy_score** - Weighted indicator score for buy signals
- ✅ **sell_score** - Weighted indicator score for sell signals
- ✅ **trigger_type** - Strategy trigger (`rsi_oversold`, `macd_cross`, etc.)
- ✅ **indicator_breakdown** - Individual indicator contributions
- ✅ **indicators_fired** - Count of positive indicators

#### Linkage Status: ✅ **FULL LINKAGE**

#### Example Strategy Triggers:
- `rsi_oversold` - RSI drops below 30
- `macd_cross` - MACD line crosses above signal
- `bb_squeeze` - Bollinger Bands narrow squeeze
- `volume_spike` - Volume > 2x average
- `multi_signal` - Multiple indicators align

#### Code References:
- **Snapshot ID generation**: `sighook/trading_strategy.py:53`
- **Score calculation**: `sighook/trading_strategy.py:150-200`
- **Metadata inclusion**: `sighook/order_manager.py:500,602,761`
- **Webhook sending**: `sighook/alerts_msgs_webhooks.py:104`
- **Metadata caching**: `webhook/listener.py:1106-1156`
- **Linkage creation**: `SharedDataManager/trade_recorder.py:408-416,1184-1278`

---

### 2. ROC Momentum Orders

**Entry Point**: `sighook/trading_strategy.py` (ROC-specific logic)

#### Flow Path:
```
sender.py:run_bot()
    ↓
trading_strategy.py:check_roc_momentum()
    ├── Calculates rate-of-change (ROC) % per symbol
    ├── Identifies momentum breakouts
    ├── Generates snapshot_id (same as strategy orders)
    ├── Sets trigger = 'roc_momo'
    └── Returns order dict with ROC-specific metadata
    ↓
order_manager.py:handle_buy_action()
    ├── Includes TP/SL specific to ROC trades (config-driven)
    ├── Builds webhook payload
    └── Sends to webhook
    ↓
[Same path as strategy orders]
```

#### Metadata Captured:
- ✅ **snapshot_id** - UUID per bot run
- ✅ **trigger_type** - `roc_momo`
- ✅ **score** - ROC percentage value
- ✅ **take_profit** - Custom TP for ROC trades
- ✅ **stop_loss** - Custom SL for ROC trades

#### Linkage Status: ✅ **FULL LINKAGE**

#### Special Characteristics:
- Often has **custom TP/SL thresholds** different from regular strategy trades
- Typically **higher conviction** trades (stronger price momentum)
- May have **larger position sizes** (configured per trigger type)

#### Code References:
- **ROC detection**: `sighook/trading_strategy.py:roc_momentum_check`
- **Custom TP/SL**: `sighook/order_manager.py:TP_SL_TRIGGER_OVERRIDES`

---

## Passive Market Making Orders

**Entry Point**: `MarketDataManager/passive_order_manager.py`

#### Flow Path:
```
webhook/listener.py:refresh_market_data() (every 30s)
    ↓
asset_monitor.py:monitor_all_orders()
    ↓
passive_order_manager.py:place_passive_orders()
    ├── Evaluates spread and profitability
    ├── Calculates optimal bid/ask prices
    ├── Places LIMIT orders on both sides
    └── Calls webhook_order_manager.place_order() directly
    ↓
COINBASE EXCHANGE (no webhook intermediary)
```

#### Metadata Captured:
- ⚠️ **snapshot_id** - ❌ NOT generated (passive orders skip sighook strategy)
- ⚠️ **score** - ❌ NOT available (no indicator evaluation)
- ✅ **trigger_type** - `passive_mm` (if captured)
- ✅ **source** - `sighook` (originated from webhook container but labeled sighook for categorization)

#### Linkage Status: 🔶 **PARTIAL LINKAGE POSSIBLE**

**Why Partial?** Passive orders bypass the sighook strategy layer, so they don't have `snapshot_id` or indicator scores. However, they could be enhanced to generate metadata at placement time.

#### Characteristics:
- **Dual-sided**: Places both buy and sell limit orders simultaneously
- **Spread-based**: Targets profitable spreads (configured min spread %)
- **Post-only**: Uses post-only orders to avoid taker fees
- **Volume-filtered**: Only trades high-volume pairs (min quote volume check)
- **Leaderboard-filtered**: Can be restricted to top-performing symbols

#### Potential Enhancement:
Could add `snapshot_id` generation and basic metadata at placement time to enable linkage tracking for passive MM performance analysis.

#### Code References:
- **Passive MM logic**: `MarketDataManager/passive_order_manager.py:293-450`
- **Profitability check**: `MarketDataManager/passive_order_manager.py:420-430`
- **Order placement**: `MarketDataManager/passive_order_manager.py:place_order()`

---

## Position Monitor Exit Orders

**Entry Point**: `MarketDataManager/position_monitor.py`

#### Flow Path:
```
webhook/listener.py:refresh_market_data() (every 30s)
    ↓
asset_monitor.py:run_positions_exit_sentinel() (every 3s)
    ↓
position_monitor.py:sweep_positions_for_exits()
    ├── For each open position:
    │   ├── Calculates unrealized P&L %
    │   ├── Checks exit conditions (priority order):
    │   │   ├── 1. Hard Stop (-5%) → MARKET order
    │   │   ├── 2. Soft Stop (-2.5%) → LIMIT exit
    │   │   ├── 3. Trailing Stop (ATR-based) → LIMIT exit
    │   │   └── 4. Signal Exit (buy_sell_matrix) → LIMIT exit (if P&L >= 0)
    │   └── If exit condition met:
    └── position_monitor.py:_place_exit_order()
        ├── Cancels conflicting orders
        ├── Builds OrderData with exit_reason
        └── Calls webhook_order_manager.place_order()
        ↓
COINBASE EXCHANGE
```

#### Metadata Captured:
- ❌ **snapshot_id** - NOT available (exit logic, not entry strategy)
- ❌ **score** - NOT available (P&L-based decision, not indicator-based)
- ⚠️ **exit_reason** - Generated but **NOT stored in database** (logged only)
- ✅ **trigger_type** - `LIMIT` (always, per current design)
- ✅ **source** - `webhook`

#### Linkage Status: ❌ **NO LINKAGE**

**Why No Linkage?** These are EXIT orders that close positions. Linkage system tracks entry strategies. Exit orders reference the original buy order via `parent_id` for FIFO calculations, not strategy metadata.

#### Exit Reasons (Logged but not in DB):
- `HARD_STOP` - Emergency -5% loss (MARKET order)
- `SOFT_STOP` - Standard -2.5% stop loss (LIMIT order)
- `TRAILING_STOP` - ATR-based trailing stop triggered
- `SIGNAL_EXIT` - buy_sell_matrix indicates SELL signal (Phase 5)
- `TP` - Take profit threshold reached (if enabled)

#### Key Configuration:
```
HARD_STOP = -0.05    # -5% emergency exit (MARKET)
STOP_LOSS = -0.025   # -2.5% soft stop (LIMIT)
TAKE_PROFIT = 0.025  # +2.5% target (currently monitored, not OCO)
TRAILING_ACTIVATION = 0.035  # +3.5% activates trailing
TRAILING_DISTANCE = 2.0 * ATR  # Distance from peak
```

#### Critical Design Notes:
1. **LIMIT-only exits** - Changed from TP/SL OCO orders to LIMIT-only for lower fees
2. **Position monitor is PRIMARY exit mechanism** - Runs every 3 seconds
3. **Multiple redundant exit paths** - Hard stop, soft stop, trailing, signal-based
4. **Exit reason NOT in database** - ⚠️ Data gap, cannot verify which path triggered from historical data

#### Code References:
- **Exit sweep**: `MarketDataManager/position_monitor.py:77-149`
- **Exit decision logic**: `MarketDataManager/position_monitor.py:151-293`
- **Exit order placement**: `MarketDataManager/position_monitor.py:420-538`
- **Trailing stop logic**: `MarketDataManager/position_monitor.py:540-662`

---

## Manual/External Orders

**Entry Point**: Coinbase Advanced Trade App, API, or Manual Entry

#### Flow Path:
```
USER → Coinbase Advanced Trade App/API
    ↓
COINBASE EXCHANGE
    ↓ (WebSocket "match" event broadcast)
    ↓
webhook/listener.py:handle_websocket_message()
    ↓
websocket_market_manager.py:process_match()
    ↓
websocket_market_manager.py:handle_order_fill()
    ↓
SharedDataManager/trade_recorder.py:record_trade()
    ├── Source = 'websocket' (detected, not originated)
    ├── No metadata available
    └── No linkage created
```

#### Metadata Captured:
- ❌ **snapshot_id** - NOT available (external order)
- ❌ **score** - NOT available
- ❌ **trigger_type** - NOT available (or generic `manual`)
- ✅ **source** - `websocket`
- ✅ **order details** - price, size, fees (from WebSocket event)

#### Linkage Status: ❌ **NO LINKAGE**

**Why No Linkage?** These orders originate outside the bot's strategy system. They are **detected and recorded** for portfolio tracking and FIFO calculations, but have no associated strategy metadata.

#### Detection Methods:
1. **WebSocket "match" events** - Real-time fill notifications
2. **REST API reconciliation** - Periodic backfill (`reconcile_with_rest_api()` every 5 minutes)
3. **Order sync** - Periodic sync of open orders (`sync_open_orders()`)

#### Use Cases:
- **Manual intervention** - User manually closes position or adds to position
- **Advanced Trade app** - User trades via Coinbase web/mobile app
- **External bots/scripts** - Other automated systems using same account
- **Emergency exits** - Manual panic sells during extreme volatility

#### Code References:
- **WebSocket detection**: `webhook/listener.py:handle_websocket_message()`
- **Match processing**: `webhook/websocket_market_manager.py:process_match()`
- **Reconciliation**: `webhook/listener.py:reconcile_with_rest_api()`

---

## Order Metadata & Linkage

### Metadata Flow for Linkage-Capable Orders

```
SIGHOOK CONTAINER:
  trading_strategy.py
    ├── current_snapshot_id = uuid.uuid4()  # Generated once per bot run
    ├── buy_score = calculate_weighted_score(indicators)
    ├── sell_score = calculate_exit_score(indicators)
    └── strategy_order = {
            'snapshot_id': str(current_snapshot_id),
            'score': {'buy_score': buy_score, 'sell_score': sell_score},
            'trigger': 'rsi_oversold'  # or other trigger type
        }
    ↓
  order_manager.py
    ├── Extracts metadata from strategy_order
    ├── Builds webhook payload:
    │   {
    │     'snapshot_id': '7f3a9c...',
    │     'score': {'Buy Score': 75.3, 'Sell Score': None},
    │     'trigger': {'trigger': 'rsi_oversold'},
    │     'pair': 'BTC-USD',
    │     'side': 'buy',
    │     ...
    │   }
    └── send_webhook(payload)
    ↓
WEBHOOK CONTAINER:
  webhook_manager.py:parse_webhook_request()
    ├── Extracts metadata from request JSON
    └── Returns trade_data dict with metadata
    ↓
  listener.py:_cache_strategy_metadata()
    ├── Stores in shared_data_manager.market_data['strategy_metadata_cache']
    ├── Key = product_id (e.g., 'BTC-USD')
    └── Cache entry:
        {
          'score': {'Buy Score': 75.3},
          'snapshot_id': '7f3a9c...',
          'trigger': 'rsi_oversold',
          'side': 'buy',
          'timestamp': 1767047523560
        }
    ↓
  [Order placed via webhook_order_manager]
    ↓
  [COINBASE fills order]
    ↓
  trade_recorder.py:record_trade()
    ├── Retrieves metadata from cache (keyed by product_id)
    ├── Calls _create_or_update_strategy_link()
    │   ├── BUY: create_strategy_link() with buy_score, snapshot_id
    │   └── SELL: update_strategy_link() with sell_score, trigger_type
    └── Clears cache entry after use (immediate TTL)
    ↓
DATABASE:
  trade_strategy_link table:
    order_id | snapshot_id | buy_score | sell_score | trigger_type | indicators_fired
```

### Metadata Cache Design

**Location**: `shared_data_manager.market_data['strategy_metadata_cache']`

**Structure**:
```python
{
    'BTC-USD': {
        'score': {'Buy Score': 75.3, 'Sell Score': None},
        'snapshot_id': '7f3a9c2b-1234-5678-90ab-cdef12345678',
        'trigger': 'rsi_oversold',
        'side': 'buy',
        'timestamp': 1767047523560
    },
    'ETH-USD': { ... }
}
```

**Lifecycle**:
1. **Created**: When webhook received with strategy metadata
2. **Read**: When trade fills and is recorded to database
3. **Deleted**: Immediately after linkage record created (TTL = immediate)

**Cache Key**: `product_id` (e.g., "BTC-USD", not order_id)

**Cache Misses** (Expected scenarios):
- Manual trades (no webhook received)
- Passive MM orders (bypass strategy layer)
- Position monitor exits (no entry metadata)
- Race condition (trade filled before webhook processed)
- Cache cleared before fill (if order takes >5 minutes to fill)

---

## Order Flow Diagrams

### Full Linkage Flow (Sighook Strategy → Trade → Linkage)

```
┌─────────────────────────────────────────────────────────────────┐
│ SIGHOOK CONTAINER (every 5 minutes)                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  [1] sender.py:run_bot()                                        │
│       ↓                                                         │
│  [2] trading_strategy.py:build_strategy_order()                │
│       • current_snapshot_id = uuid.uuid4()                     │
│       • buy_score = Σ(indicator_weights)                       │
│       • trigger = identify_primary_signal()                     │
│       ↓                                                         │
│  [3] order_manager.py:handle_buy_action()                      │
│       • payload = build_webhook_payload(                        │
│             snapshot_id, score, trigger, ...)                   │
│       ↓                                                         │
│  [4] alerts_msgs_webhooks.py:send_webhook()                    │
│       • POST http://webhook:5003/webhook                        │
│                                                                 │
└────────────┬────────────────────────────────────────────────────┘
             │
             │ HTTP POST
             ↓
┌─────────────────────────────────────────────────────────────────┐
│ WEBHOOK CONTAINER                                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  [5] webhook_manager.py:parse_webhook_request()                │
│       • extract snapshot_id, score, trigger                     │
│       ↓                                                         │
│  [6] listener.py:_cache_strategy_metadata()                    │
│       • cache[product_id] = {metadata}                          │
│       ↓                                                         │
│  [7] webhook_order_manager.py:place_order()                    │
│       • build OrderData                                         │
│       • submit to Coinbase API                                  │
│                                                                 │
└────────────┬────────────────────────────────────────────────────┘
             │
             │ REST API
             ↓
┌─────────────────────────────────────────────────────────────────┐
│ COINBASE EXCHANGE                                               │
│  • Order placed                                                 │
│  • Order fills (match event)                                    │
└────────────┬────────────────────────────────────────────────────┘
             │
             │ WebSocket "match" event
             ↓
┌─────────────────────────────────────────────────────────────────┐
│ WEBHOOK CONTAINER (WebSocket handler)                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  [8] listener.py:handle_websocket_message()                    │
│       ↓                                                         │
│  [9] websocket_market_manager.py:process_match()               │
│       ↓                                                         │
│  [10] trade_recorder.py:record_trade()                         │
│        • retrieve cache[product_id]                             │
│        ↓                                                        │
│  [11] trade_recorder.py:_create_or_update_strategy_link()     │
│        • BUY: create_strategy_link(snapshot_id, buy_score)     │
│        • SELL: update_strategy_link(sell_score, trigger)       │
│        • Clear cache[product_id]                               │
│                                                                 │
└────────────┬────────────────────────────────────────────────────┘
             │
             │ Database INSERT/UPDATE
             ↓
┌─────────────────────────────────────────────────────────────────┐
│ DATABASE (PostgreSQL)                                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  trade_records:                                                 │
│    order_id, symbol, side, price, size, ...                     │
│                                                                 │
│  trade_strategy_link: ✅ LINKED                                │
│    order_id, snapshot_id, buy_score, sell_score, trigger_type  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### No Linkage Flow (Position Monitor Exit)

```
┌─────────────────────────────────────────────────────────────────┐
│ WEBHOOK CONTAINER (every 3 seconds)                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  [1] asset_monitor.py:run_positions_exit_sentinel()           │
│       ↓                                                         │
│  [2] position_monitor.py:sweep_positions_for_exits()          │
│       • unrealized_pnl_pct = (current - entry) / entry         │
│       • if pnl_pct <= -0.025: exit_reason = "SOFT_STOP"       │
│       ↓                                                         │
│  [3] position_monitor.py:_place_exit_order(reason="SOFT_STOP")│
│       • build OrderData (NO snapshot_id, NO score)             │
│       ↓                                                         │
│  [4] webhook_order_manager.py:place_order()                    │
│       • submit LIMIT sell to Coinbase                           │
│                                                                 │
└────────────┬────────────────────────────────────────────────────┘
             │
             │ REST API
             ↓
┌─────────────────────────────────────────────────────────────────┐
│ COINBASE EXCHANGE                                               │
│  • SELL order fills                                             │
└────────────┬────────────────────────────────────────────────────┘
             │
             │ WebSocket "match" event
             ↓
┌─────────────────────────────────────────────────────────────────┐
│ WEBHOOK CONTAINER                                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  [5] trade_recorder.py:record_trade()                          │
│       • retrieve cache[product_id] → NOT FOUND                  │
│       • Skip linkage (graceful degradation)                     │
│       • Log: "No metadata cached for BTC-USD, skipping linkage"│
│                                                                 │
└────────────┬────────────────────────────────────────────────────┘
             │
             │ Database INSERT only
             ↓
┌─────────────────────────────────────────────────────────────────┐
│ DATABASE                                                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  trade_records: ✅ RECORDED                                     │
│    order_id, symbol='BTC-USD', side='sell', source='webhook'   │
│                                                                 │
│  trade_strategy_link: ❌ NO RECORD (exit order, no linkage)    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Summary Table

| Order Type | Origin | Has Metadata | Linkage | Source Label | Trigger Example |
|------------|--------|--------------|---------|--------------|-----------------|
| Strategy Indicator | sighook/trading_strategy.py | ✅ Full | ✅ Yes | `sighook` | `rsi_oversold` |
| ROC Momentum | sighook/trading_strategy.py | ✅ Full | ✅ Yes | `sighook` | `roc_momo` |
| Passive MM | MarketDataManager/passive_order_manager.py | ⚠️ Partial | 🔶 Possible | `sighook` | `passive_mm` |
| Hard Stop Exit | MarketDataManager/position_monitor.py | ❌ No | ❌ No | `webhook` | `HARD_STOP`* |
| Soft Stop Exit | MarketDataManager/position_monitor.py | ❌ No | ❌ No | `webhook` | `SOFT_STOP`* |
| Trailing Stop | MarketDataManager/position_monitor.py | ❌ No | ❌ No | `webhook` | `TRAILING_STOP`* |
| Signal Exit | MarketDataManager/position_monitor.py | ❌ No | ❌ No | `webhook` | `SIGNAL_EXIT`* |
| Manual/External | Coinbase App/API | ❌ No | ❌ No | `websocket` | `manual` |

\* *Exit reasons are logged but NOT stored in database (current data gap)*

---

## Key Insights for Optimization Analysis

### Linkage-Capable Orders (Can Analyze)
✅ **Sighook strategy trades** - Full metadata → Can correlate buy_score/sell_score with outcomes
✅ **ROC momentum trades** - Full metadata → Can analyze ROC trigger performance
🔶 **Passive MM trades** - Could be enhanced → Currently limited analysis

### Non-Linkable Orders (Cannot Analyze via Linkage)
❌ **Position monitor exits** - P&L-based decisions, not strategy-based
❌ **Manual trades** - External to bot strategy system

### Current Linkage Rate Drivers

Based on the first report showing **0% linkage (0/18 trades)**:
- All 18 trades were `source='websocket'` (manual/external trades)
- Zero `source='sighook'` trades executed since deployment
- **Expected behavior** - linkage system working correctly, just no strategy trades yet

**For >90% linkage rate**, the system needs:
1. Active sighook strategy execution (currently running but not triggering)
2. Fewer manual interventions (user trading less via app)
3. Sighook signals meeting entry criteria (market conditions dependent)

---

**Document Version**: 1.0
**Last Updated**: December 30, 2025
**Maintained By**: BotTrader Development Team
