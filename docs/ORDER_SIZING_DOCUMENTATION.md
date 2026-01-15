# BotTrader Order Sizing System - Technical Documentation

**Date**: January 4, 2026
**Purpose**: Comprehensive guide to how order sizing works across all order types
**Related**: [ORDER_FLOW_DOCUMENTATION.md](ORDER_FLOW_DOCUMENTATION.md), [ARCHITECTURE_DEEP_DIVE.md](active/architecture/ARCHITECTURE_DEEP_DIVE.md)

---

## Table of Contents

1. [Overview](#overview)
2. [Configuration (.env)](#configuration-env)
3. [Order Sizing Flow](#order-sizing-flow)
4. [Trigger-Based Sizing Logic](#trigger-based-sizing-logic)
5. [Code Architecture](#code-architecture)
6. [Troubleshooting](#troubleshooting)
7. [Historical Issues](#historical-issues)

---

## Overview

The BotTrader system uses a **trigger-based order sizing system** that automatically determines the appropriate order size based on the type of trade signal. This allows for:

- **Visual identification** of order types in exchange history (different sizes = different strategies)
- **Risk management** via position sizing per strategy type
- **Performance tracking** by correlating order size to strategy performance
- **Flexible configuration** through environment variables

### Key Principle

**Order sizes are determined by the TRIGGER TYPE, not the source container.**

All strategy orders from sighook are sent via webhook to the webhook container, where the trigger type is evaluated and the appropriate order size is applied.

---

## Configuration (.env)

### Order Size Environment Variables

```bash
# Base/Default Order Size (fallback)
ORDER_SIZE_FIAT=35.00

# Strategy-Specific Order Sizes
ORDER_SIZE_SIGNAL=15.00    # Technical indicator signals (RSI, MACD, BB, etc.)
ORDER_SIZE_ROC=20.00       # Rate-of-change momentum trades
ORDER_SIZE_PASSIVE=32.00   # Passive market making (dual-sided quotes)
ORDER_SIZE_WEBHOOK=25.00   # External webhook signals (deprecated/fallback)
```

### What Each Variable Controls

| Variable | Used For | Example Triggers | Notes |
|----------|----------|------------------|-------|
| `ORDER_SIZE_SIGNAL` | Technical indicator-based strategy orders | `score`, `signal`, `rsi_oversold`, `macd_cross`, `bb_squeeze` | Most common strategy orders |
| `ORDER_SIZE_ROC` | Momentum breakout trades | `ROC`, `ROC_MOMO`, `ROC_MOMO_OVERRIDE` | Higher conviction trades, peak tracking enabled |
| `ORDER_SIZE_PASSIVE` | Passive market making | `PASSIVE_BUY`, `PASSIVE_SELL`, `PASSIVE_EXIT` | Dual-sided limit orders |
| `ORDER_SIZE_WEBHOOK` | External signals (deprecated) | (any trigger not in map) | Fallback for unknown trigger types |
| `ORDER_SIZE_FIAT` | Base default | (when all else fails) | Ultimate fallback |

---

## Order Sizing Flow

### Complete Flow from Signal to Order

```
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 1: SIGHOOK CONTAINER - Signal Generation                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  sighook/trading_strategy.py:decide_action()                          │
│    ├── Evaluates technical indicators                                  │
│    ├── Calculates buy_score / sell_score                              │
│    ├── Determines trigger type (e.g., "rsi_oversold", "ROC_MOMO")     │
│    └── Returns strategy_order dict                                     │
│         {                                                               │
│           "trigger": "rsi_oversold",                                   │
│           "score": {"Buy Score": 75.3},                                │
│           "snapshot_id": "uuid-here",                                  │
│           ...                                                           │
│         }                                                               │
│         ↓                                                               │
│  sighook/order_manager.py:handle_buy_action()                         │
│    ├── Receives strategy_order from trading_strategy                   │
│    ├── Extracts trigger, score, snapshot_id                           │
│    └── Calls build_webhook_payload()                                   │
│         ↓                                                               │
│  sighook/order_manager.py:build_webhook_payload()                     │
│    ├── Creates webhook payload                                         │
│    ├── Sets order_amount_fiat = None  ← CRITICAL!                     │
│    │   (Lets webhook container determine size based on trigger)       │
│    ├── Includes trigger metadata                                       │
│    └── Returns payload dict                                            │
│                                                                         │
└────────────┬────────────────────────────────────────────────────────────┘
             │
             │ HTTP POST to webhook:5003/webhook
             ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 2: WEBHOOK CONTAINER - Webhook Reception                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  webhook/listener.py:handle_webhook()                                  │
│    └── Receives HTTP POST with JSON payload                            │
│         ↓                                                               │
│  webhook/webhook_manager.py:parse_webhook_request()                   │
│    ├── Extracts all fields from webhook JSON                          │
│    ├── Checks order_amount_fiat in request                            │
│    │   if order_amount_fiat is not None:                              │
│    │       use that value                                              │
│    │   else:                                                           │
│    │       set to self._order_size_fiat (fallback)                    │
│    └── Returns normalized trade_data dict                              │
│         ↓                                                               │
│  webhook/listener.py:process_webhook()                                │
│    └── Calls trade_order_manager.build_order_data()                   │
│                                                                         │
└────────────┬────────────────────────────────────────────────────────────┘
             │
             ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 3: ORDER MANAGER - Trigger-Based Sizing                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  webhook/webhook_order_manager.py:build_order_data()                  │
│    ├── Line 376: Calls get_order_size_for_trigger()                   │
│    │   trigger_order_size = self.get_order_size_for_trigger(trigger)  │
│    │                                                                    │
│    │   ↓                                                                │
│    │   webhook/webhook_order_manager.py:get_order_size_for_trigger()  │
│    │   (also known as _determine_order_size())                        │
│    │   ├── Lines 586-613: Trigger type mapping logic                  │
│    │   ├── Extracts trigger_type from trigger dict                    │
│    │   │   trigger_type = trigger.get("trigger", "").upper()          │
│    │   │                                                                │
│    │   ├── Maps trigger to order size:                                │
│    │   │   trigger_size_map = {                                        │
│    │   │       "ROC": config.order_size_roc,                          │
│    │   │       "PASSIVE_BUY": config.order_size_passive,              │
│    │   │       "PASSIVE_SELL": config.order_size_passive,             │
│    │   │       "SIGNAL": config.order_size_signal,                    │
│    │   │       "SCORE": config.order_size_signal,                     │
│    │   │       "ROC_MOMO": config.order_size_roc,                     │
│    │   │       "ROC_MOMO_OVERRIDE": config.order_size_roc,            │
│    │   │   }                                                            │
│    │   │                                                                │
│    │   ├── Looks up size in map:                                      │
│    │   │   size = trigger_size_map.get(                               │
│    │   │       trigger_type,                                           │
│    │   │       config.order_size_webhook  ← fallback                 │
│    │   │   )                                                           │
│    │   │                                                                │
│    │   └── Returns Decimal(size)                                       │
│    │                                                                    │
│    ├── Uses trigger_order_size for order amount                       │
│    └── Continues order construction                                    │
│                                                                         │
└────────────┬────────────────────────────────────────────────────────────┘
             │
             ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 4: ORDER PLACEMENT                                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  Order placed to Coinbase with correct trigger-based size              │
│                                                                         │
│  Examples:                                                              │
│    • trigger="rsi_oversold"  → $15 (ORDER_SIZE_SIGNAL)                │
│    • trigger="ROC_MOMO"      → $20 (ORDER_SIZE_ROC)                   │
│    • trigger="PASSIVE_BUY"   → $32 (ORDER_SIZE_PASSIVE)               │
│    • trigger="unknown"       → $25 (ORDER_SIZE_WEBHOOK fallback)      │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Trigger-Based Sizing Logic

### Trigger Type to Order Size Mapping

**Code Location:** `webhook/webhook_order_manager.py:586-613`

```python
def get_order_size_for_trigger(self, trigger: dict) -> Decimal:
    """
    Determine order size based on trigger type for visual identification.

    Args:
        trigger: Trigger dict with 'trigger' and 'trigger_note' keys

    Returns:
        Decimal: Order size in USD
    """
    if not trigger or not isinstance(trigger, dict):
        return Decimal(str(self.config.order_size_fiat or 35))

    trigger_type = trigger.get("trigger", "").upper()

    # Map trigger types to specific order sizes
    trigger_size_map = {
        "ROC": self.config.order_size_roc,
        "PASSIVE_BUY": self.config.order_size_passive,
        "PASSIVE_SELL": self.config.order_size_passive,
        "PASSIVE_EXIT": self.config.order_size_passive,
        "PASSIVE_PROFIT": self.config.order_size_passive,
        "PASSIVE_LOSS": self.config.order_size_passive,
        "SIGNAL": self.config.order_size_signal,
        "SCORE": self.config.order_signal,
        "ROC_MOMO": self.config.order_size_roc,
        "ROC_MOMO_OVERRIDE": self.config.order_size_roc,
    }

    # Get size from map or default to webhook size
    size = trigger_size_map.get(trigger_type, self.config.order_size_webhook)

    # Fallback to default if not configured
    if not size:
        size = self.config.order_size_fiat or Decimal('35')

    return Decimal(str(size))
```

### Fallback Chain

When determining order size, the system follows this priority:

1. **Trigger-specific size** (from `trigger_size_map`) - **PREFERRED**
2. **Webhook default** (`ORDER_SIZE_WEBHOOK`) - if trigger not in map
3. **Base default** (`ORDER_SIZE_FIAT`) - if webhook size not configured
4. **Hardcoded $35** - ultimate fallback

### Example Trigger Mappings

| Strategy Signal | Trigger Type | Maps To | Order Size |
|----------------|--------------|---------|------------|
| RSI Oversold | `rsi_oversold` | `SIGNAL` | `ORDER_SIZE_SIGNAL` |
| MACD Cross | `macd_cross` | `SIGNAL` | `ORDER_SIZE_SIGNAL` |
| Bollinger Squeeze | `bb_squeeze` | `SIGNAL` | `ORDER_SIZE_SIGNAL` |
| Multi-Signal | `multi_signal` | `SIGNAL` | `ORDER_SIZE_SIGNAL` |
| Generic Score | `score` | `SCORE` | `ORDER_SIZE_SIGNAL` |
| ROC Momentum | `ROC_MOMO` | `ROC_MOMO` | `ORDER_SIZE_ROC` |
| ROC Override | `ROC_MOMO_OVERRIDE` | `ROC_MOMO_OVERRIDE` | `ORDER_SIZE_ROC` |
| ROC Generic | `ROC` | `ROC` | `ORDER_SIZE_ROC` |
| Passive MM Buy | `PASSIVE_BUY` | `PASSIVE_BUY` | `ORDER_SIZE_PASSIVE` |
| Unknown/External | `anything_else` | (no match) | `ORDER_SIZE_WEBHOOK` |

---

## Code Architecture

### Key Files and Their Roles

#### 1. Sighook Container - Signal Generation

**File:** `sighook/order_manager.py`
**Function:** `build_webhook_payload()` (lines 701-771)

**Responsibility:**
- Constructs webhook payload for strategy orders
- **Does NOT determine order size** - sets `order_amount_fiat = None`
- Includes trigger metadata for downstream processing

**Critical Code:**
```python
# Line 754 (FIXED as of Jan 4, 2026)
"order_amount_fiat": None if side.lower() == "buy" else base_avail_to_trade,
```

**Previous Bug:**
```python
# OLD (HARDCODED - DO NOT USE):
"order_amount_fiat": float(20.00) if side.lower() == "buy" else base_avail_to_trade,
```

---

#### 2. Webhook Container - Payload Parsing

**File:** `webhook/webhook_manager.py`
**Function:** `parse_webhook_request()` (lines 165-229)

**Responsibility:**
- Parses incoming webhook JSON
- Extracts `order_amount_fiat` if provided
- Falls back to `_order_size_fiat` if `order_amount_fiat` is `None`

**Critical Code:**
```python
# Lines 195-201
raw_order_amount = request_json.get("order_amount_fiat")
if raw_order_amount is not None:
    order_amount_fiat = Decimal(str(raw_order_amount))
else:
    # Fallback to default bot-configured order size
    order_amount_fiat = getattr(self, "_order_size_fiat", Decimal("0"))
```

**Note:** This sets a fallback, but the actual trigger-based sizing happens later in `build_order_data()`.

---

#### 3. Order Manager - Trigger-Based Sizing

**File:** `webhook/webhook_order_manager.py`
**Function:** `build_order_data()` (lines 299-518)

**Responsibility:**
- Builds complete `OrderData` object
- **Calls `get_order_size_for_trigger()` to determine final order size**

**Critical Code:**
```python
# Line 376
trigger_order_size = self.get_order_size_for_trigger(trigger if isinstance(trigger, dict) else {})
fiat_amt = min(usd_avail, trigger_order_size)
```

---

**File:** `webhook/webhook_order_manager.py`
**Function:** `get_order_size_for_trigger()` (also named `_determine_order_size()`) (lines 586-613)

**Responsibility:**
- **THE AUTHORITATIVE SOURCE for order sizing**
- Maps trigger types to `.env` order size variables
- Returns final order size as `Decimal`

**Critical Code:** (See [Trigger-Based Sizing Logic](#trigger-based-sizing-logic) section above)

---

### Passive Market Making (Special Case)

**File:** `MarketDataManager/passive_order_manager.py`

**Flow:**
- Does **NOT** go through sighook container
- Calls `trade_order_manager.build_order_data()` directly
- Provides `trigger = {"trigger": "PASSIVE_BUY"}` or `{"trigger": "PASSIVE_SELL"}`
- Trigger-based sizing automatically applies `ORDER_SIZE_PASSIVE`

**Key Difference:**
- Passive orders bypass the webhook HTTP endpoint
- Still use the same `build_order_data()` flow
- Same trigger-based sizing logic applies

---

## Troubleshooting

### Common Issues

#### Issue 1: All orders are the same size

**Symptom:** Every order is $20 (or some other fixed amount) regardless of trigger type.

**Diagnosis:**
```bash
# Check if sighook is hardcoding order_amount_fiat
grep -n "order_amount_fiat.*20.00" sighook/order_manager.py
```

**Fix:** Ensure `sighook/order_manager.py:754` sets `order_amount_fiat = None` for buy orders.

---

#### Issue 2: Orders are using ORDER_SIZE_WEBHOOK instead of ORDER_SIZE_SIGNAL

**Symptom:** Signal-based orders are $25 instead of $15.

**Possible Causes:**

1. **Trigger type not in map**
   - Check trigger type being sent: `grep -A 5 "trigger.*:" logs/webhook.log`
   - Verify trigger type exists in `trigger_size_map` (webhook_order_manager.py:593-604)

2. **Trigger format incorrect**
   - Should be: `{"trigger": "rsi_oversold"}`
   - NOT: `"rsi_oversold"` (string instead of dict)

**Fix:** Add missing trigger type to map or fix trigger format in signal generation.

---

#### Issue 3: Trigger-based sizing not being called

**Symptom:** Orders use fallback sizes even when trigger is correct.

**Diagnosis:**
```bash
# Check if get_order_size_for_trigger is being called
grep "get_order_size_for_trigger" logs/webhook.log
```

**Possible Causes:**
- `order_amount_fiat` already set in webhook payload (bypassing trigger logic)
- `build_order_data()` not being called (using old order flow)

**Fix:**
- Ensure sighook sets `order_amount_fiat = None`
- Verify `build_order_data()` path is being used (check logs)

---

### Debugging Order Sizing

#### Step 1: Check Webhook Payload

Add debug logging to see what sighook is sending:

**File:** `sighook/order_manager.py` (after line 763)

```python
self.logger.debug(f"[ORDER_SIZE_DEBUG] Webhook payload for {symbol}: order_amount_fiat={payload.get('order_amount_fiat')}, trigger={payload.get('trigger')}")
```

**Expected Output:**
```
[ORDER_SIZE_DEBUG] Webhook payload for BTC-USD: order_amount_fiat=None, trigger={'trigger': 'rsi_oversold'}
```

---

#### Step 2: Check Trigger-Based Sizing

Add debug logging to see what size is being applied:

**File:** `webhook/webhook_order_manager.py` (in `get_order_size_for_trigger()` after line 607)

```python
self.logger.debug(f"[ORDER_SIZE_DEBUG] Trigger type: {trigger_type}, Mapped size: {size}, Config values: SIGNAL={self.config.order_size_signal}, ROC={self.config.order_size_roc}, WEBHOOK={self.config.order_size_webhook}")
```

**Expected Output:**
```
[ORDER_SIZE_DEBUG] Trigger type: RSI_OVERSOLD, Mapped size: 15.00, Config values: SIGNAL=15.00, ROC=20.00, WEBHOOK=25.00
```

---

#### Step 3: Verify .env Configuration

```bash
# Check all order size variables are set
grep ORDER_SIZE .env
```

**Expected Output:**
```
ORDER_SIZE_FIAT=35.00
ORDER_SIZE_PASSIVE=32.00
ORDER_SIZE_WEBHOOK=25.00
ORDER_SIZE_ROC=20.00
ORDER_SIZE_SIGNAL=15.00
```

---

#### Step 4: Check Trade Records

Query recent orders to see actual sizes used:

```sql
SELECT
    symbol,
    side,
    trigger,
    size * price as notional_usd,
    order_time
FROM trade_records
WHERE order_time > NOW() - INTERVAL '1 hour'
ORDER BY order_time DESC
LIMIT 20;
```

**Analysis:**
- `notional_usd ≈ 15` → Using `ORDER_SIZE_SIGNAL`
- `notional_usd ≈ 20` → Using `ORDER_SIZE_ROC`
- `notional_usd ≈ 32` → Using `ORDER_SIZE_PASSIVE`
- `notional_usd ≈ 25` → Using `ORDER_SIZE_WEBHOOK` (fallback)

---

## Historical Issues

### Issue: Hardcoded $20 Order Size (Fixed: Jan 4, 2026)

**Discovered:** User noticed orders at ~$25 and ~$15 when expecting consistent trigger-based sizing.

**Root Cause:** `sighook/order_manager.py:751` had hardcoded value:
```python
"order_amount_fiat": float(20.00) if side.lower() == "buy" else base_avail_to_trade,
```

This was left over from debugging and commented as `#debugging`, indicating it was meant to be temporary.

**Impact:**
- All sighook strategy orders used $20 regardless of trigger type
- Trigger-based sizing in webhook container was bypassed
- Made it impossible to visually identify strategy types by order size
- Performance tracking by strategy was compromised

**Fix:** Changed to:
```python
"order_amount_fiat": None if side.lower() == "buy" else base_avail_to_trade,
```

**Verification:**
- Test orders with different triggers
- Confirm sizes match `.env` configuration
- Check trade_records for proper sizing distribution

---

### Deprecated: External Webhook Signals (TradingView)

**Status:** Deprecated, no longer in use.

**Variable:** `ORDER_SIZE_WEBHOOK=25.00`

**Current Use:** Fallback for unknown trigger types only.

**Future Consideration:** May be removed or repurposed in future refactoring.

---

## Summary

### Key Takeaways

1. **Order sizing is trigger-based**, not source-based
2. **Sighook NEVER determines order size** - it only provides trigger metadata
3. **Webhook container determines final size** via `get_order_size_for_trigger()`
4. **Trigger types map to .env variables** for flexible configuration
5. **`order_amount_fiat` should be `None`** in sighook webhook payloads for buy orders

### Configuration Best Practices

1. **Set all ORDER_SIZE_* variables** in `.env` even if using defaults
2. **Use distinct values** for each order type for visual identification
3. **Document any changes** to default values with rationale
4. **Test sizing** after .env changes with small test orders
5. **Monitor trade_records** to verify correct sizing in production

### Adding New Trigger Types

To add a new trigger type with custom order sizing:

1. **Define trigger in strategy code:**
   ```python
   # sighook/trading_strategy.py
   trigger = "my_new_strategy"
   ```

2. **Add .env variable (optional):**
   ```bash
   ORDER_SIZE_MY_STRATEGY=18.00
   ```

3. **Add to trigger map:**
   ```python
   # webhook/webhook_order_manager.py:593-604
   trigger_size_map = {
       ...
       "MY_NEW_STRATEGY": self.config.order_size_my_strategy,
   }
   ```

4. **Update config manager:**
   ```python
   # Config/config_manager.py
   "_order_size_my_strategy": "ORDER_SIZE_MY_STRATEGY",
   ```

---

**Document Version:** 1.0
**Last Updated:** January 4, 2026
**Maintained By:** BotTrader Development Team
