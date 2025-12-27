# Review ROC Strategy Session
**Started:** 2025-12-24 08:30 PT (2025-12-24 16:30 UTC)

## Session Overview
This session focuses on reviewing the Rate of Change (ROC) indicator strategy implementation and its impact on trading performance.

---

## Goals

- [x] Review current ROC indicator implementation
- [x] Analyze ROC threshold configuration (buy/sell triggers)
- [x] Evaluate ROC's contribution to signal scoring
- [x] Identify potential optimizations or issues
- [ ] Compare ROC performance vs other indicators

---

## Key Files

| File | Purpose |
|------|---------|
| `sighook/signal_manager.py` | ROC indicator integration, thresholds |
| `sighook/indicators.py` | ROC calculation logic |
| `sighook/trading_strategy.py` | ATR caching for TP/SL (NEW) |
| `webhook/webhook_order_manager.py` | Trigger-specific TP/SL (MODIFIED) |
| `ProfitDataManager/profit_data_manager.py` | TP/SL calculation (MODIFIED) |
| `MarketDataManager/ticker_manager.py` | ATR calculation (for existing positions only) |
| `.env` | Runtime threshold values |

---

## Progress Log

- **08:30 PT** - Session started
- **09:00 PT** - Analyzed SQD-USD trading history, found ROC buy signals DID fire
- **09:30 PT** - Discovered root cause: positions closing too quickly due to tight TP/SL
- **10:00 PT** - Found critical bug: ATR = 0 for new buys (only calculated for existing positions)
- **10:30 PT** - Implemented ATR caching in sighook for all evaluated symbols
- **11:00 PT** - Implemented trigger-specific TP/SL multipliers for ROC_MOMO trades
- **11:30 PT** - All tests passed, changes complete

---

## Findings

### Root Cause Analysis (SQD-USD Trade)

**Symptom:** SQD-USD trades were not profitable despite large intraday moves (57% potential gain)

**Issue 1: ROC Buy Signals WERE Firing**
- Multiple ROC buy signals fired via `roc_momo_override` trigger
- Signals at 06:27, 06:47, 07:12, 08:42 UTC
- This was NOT an entry problem - the signals worked correctly

**Issue 2: Positions Closing Too Quickly (2-18 minutes)**
- TP/SL were too tight for momentum trades:
  - TP: 2.5% (hit quickly)
  - SL: ~2% (with cushions)
- Momentum trades need room to run (10-50%+ moves expected)

**Issue 3: ATR = 0 for New Buys (ROOT CAUSE)**
- `ticker_manager.py:584-588` only calculates ATR for symbols with EXISTING positions
- When entering a NEW position (like SQD-USD), there's no ATR data
- TP/SL falls back to `STOP_MIN_PCT` (1.2%) + cushions = ~2% stop

---

## Changes Made

### 1. ATR Caching in Sighook (`sighook/trading_strategy.py`)
Added `_cache_atr_for_symbol()` method that:
- Calculates proper True Range ATR for ALL evaluated symbols
- Caches in `shared_data_manager.market_data['atr_pct_cache']`
- Now webhook can use ATR for new buys (not just existing positions)

### 2. Trigger-Specific TP/SL (`webhook/webhook_order_manager.py`)
Added methods:
- `_get_trigger_tp_multiplier()` - Returns TP multiplier based on trigger
- `_get_trigger_sl_multiplier()` - Returns SL multiplier based on trigger

For ROC_MOMO/ROC_MOMO_OVERRIDE/ROC triggers:
- TP multiplier: 3x (env var: `ROC_TP_MULTIPLIER`)
- SL multiplier: 2x (env var: `ROC_SL_MULTIPLIER`)

Example impact:
- Normal trigger: TP=2.5%, SL=2.4%
- ROC_MOMO trigger: TP=7.5%, SL=4.8%

### 3. TP/SL Logging (`ProfitDataManager/profit_data_manager.py`)
Updated `calculate_tp_sl()` to:
- Apply trigger-specific multipliers
- Log trigger type and multipliers in tpsl.jsonl

---

## Configuration

New environment variables (optional):
```env
# Trigger-specific TP/SL multipliers for momentum trades
ROC_TP_MULTIPLIER=3.0  # 3x TP for ROC momentum trades (default: 3.0)
ROC_SL_MULTIPLIER=2.0  # 2x SL for ROC momentum trades (default: 2.0)
```

---

## Notes

- ATR is now cached for ALL symbols evaluated by sighook, not just existing positions
- This ensures new ROC momentum trades get proper ATR-based stops
- The trigger-specific multipliers allow momentum trades to run longer
- Logging now includes trigger type for post-trade analysis

