# BotTrader Project Overview

**Last Updated**: February 4, 2026

---

## Executive Summary

BotTrader is a **containerized cryptocurrency trading bot** running on AWS, executing a **maker-first LIMIT order strategy** on Coinbase Advanced Trade or Kraken exchange. The system uses a dual-container architecture (webhook for execution, sighook for signals) with shared PostgreSQL state. Recent development focuses on **fee-aware strategy design** to overcome Coinbase's 0.6% round-trip fees (0.4% maker + 0.8% taker).

---

## Production Architecture

### Deployment (AWS EC2)
- **3 Docker containers**: webhook (execution), sighook (signals), db (PostgreSQL 16)
- **Location**: `/opt/bot` (git-based deployment)
- **Branch**: `feature/strategy-optimization`
- **Deployment method**: Git pull (NOT rsync)

### Container Roles

**Webhook** (Order Execution):
- WebSocket listener for fills and order updates
- Position monitor with multi-path exits
- Consumes signals from sighook via shared database

**Sighook** (Signal Generation):
- Technical indicator calculation (RSI, MACD, ROC, Bollinger)
- Creates `buy_sell_matrix` (BUY/SELL/HOLD signals per symbol)
- Persists signals to database for webhook consumption

**Database**:
- PostgreSQL with key tables: `trade_records`, `fifo_allocations`, `passive_orders`, `shared_data`
- FIFO accounting engine for tax-compliant P&L
- Cross-container state synchronization

---

## Production Trading Strategy

### Entry Strategy
- **LIMIT orders only** (post-only for 0.4% maker fees)
- Signal-driven from sighook indicators
- Multiple signal types: ROC momentum, composite scoring

### Exit Strategy (Multi-Path)

**1. Risk Exits**:
- Hard stop: -5% (MARKET order emergency)
- Soft stop: -2.5% (LIMIT order)

**2. Signal + Profit Exit** (Phase 5 - Nov 30, 2025):
- Monitors `buy_sell_matrix` from sighook
- Exits on SELL signal ONLY if P&L ≥ 0%
- Prevents panic selling during drawdowns

**3. ATR-Based Trailing Stop**:
- Activates at +3.5% profit
- 2×ATR distance below highest price
- Constrained within 1-2% bounds

**4. Peak Tracking** (ROC momentum trades):
- Activates at +6% profit
- Exits on -5% drawdown from peak
- 24-hour max hold time

### Key Production Features
- **Dynamic Symbol Filtering**: Auto-exclude underperformers (win rate, spread, P&L)
- **Passive Market Making**: Break-even exits, time-based exits
- **WebSocket Stability**: DNS refresh, 90s watchdog, exponential backoff

---

## Backtest Development

### Current Focus: 4h Hybrid Maker Strategy

**Timeline**: Jan-Feb 2026 (Phase 2.1-2.3 ongoing)

**Why 4h Timeframe?**
- Previous 15m strategies failed due to high turnover
- 0.6% round-trip fees destroyed edge on short timeframes
- 4h candles reduce trade frequency, improve maker fill rates

### Strategy Design

**Setup Generation**:
- **Option A**: Donchian breakout (20-period high)
- **Option B**: Vol-adjusted ROC score (momentum/ATR normalization)

**Filters**:
- Regime: 1D EMA200 (only long in uptrends)
- Viability: ATR% ≥ 2× round-trip fees (0.8%)
- Compression: Bollinger Band width < 30th percentile

**Entry Execution** (Maker-Friendly):
1. **Retest Entry**: Wait for pullback after breakout (patient, better fills)
2. **Chase Entry**: If retest expires, chase with dynamic ATR offset
3. **Chase Hardening** (Phase 2.1):
   - Max extension: 0.5% from breakout (prevents runaway chases)
   - Expansion confirmation: BB width must be increasing (volatility breakout)

**Profit Taking** (Fee-Multiple Targets):
- TP1: 2× fees (+0.8%) - scale 40%
- TP2: 4× fees (+1.6%) - scale 40%
- Runner: 20% with ATR-based trailing stop

**State Machine**:
```
FLAT → SETUP_ACTIVE → RETEST_ORDER_WORKING → CHASE_ORDER_WORKING → IN_POSITION → FLAT
```

**Guardrails** (No Concurrent Setups):
- Max 1 active setup per symbol
- No setup queue (locked when IN_POSITION or SETUP_ACTIVE)
- "Production now, upgrade door later" approach

### Phase 2.3 Optimization (Current Work)

**Objectives**:
- ROC threshold calibration (target: 60-120 setups/year)
- Compression threshold tuning (BB width percentile)
- Entry offset optimization (retest/chase pricing)

**Stage C** (Compression-Based Runner Policy):
- Adaptive runner size/trailing based on entry compression context
- Hypothesis: Compressed entries need tighter trails (tested Feb 3)
- **Surprising Finding**: Compressed entries OUTPERFORM (60% vs 17% win rate)

**Recent Fixes**:
- Stage C warmup logging (NaN arrays → "WARMUP" messages)
- ROC percentile calibration (NaN filtering for 360d window)

### Test Windows
- **60d**: Quick validation
- **180d**: Standard test
- **360d**: Long-term robustness

---

## Historical Backtest Strategies (Archived)

### Multi-ROC Strategy (Failed)
- **Result**: +$17.65 gross, -$424.91 fees, -$407.25 net (60d BTC)
- **Root Cause**: High turnover incompatible with 0.6% fees
- **Exception**: BTC showed 60% win rate → inspired 4h hybrid

### Multi-Timeframe Experimental
- 15m momentum strategies (Donchian, ROC, peak drawdown)
- **Conclusion**: Timeframe too short for Coinbase fee environment

---

## Key Architectural Patterns

### Cross-Container Communication
- **Mechanism**: Shared PostgreSQL `shared_data` table
- **Format**: JSONB serialized Python dicts
- **Key Data**: `buy_sell_matrix` (signals), `ticker_cache` (prices), `spot_positions`

### FIFO Accounting
- **Purpose**: Tax-compliant P&L calculations
- **Timing**: Batch recalculation on startup (NOT real-time)
- **Critical**: Cannot use `pnl_usd` for live TP/SL (position monitor uses current price instead)

### WebSocket Management
- DNS refresh before reconnection (load balancer changes)
- 90s idle watchdog prevents zombie connections
- Email alerts after 10 failed reconnection attempts

---

## Fee Awareness (Central Theme)

Every recent development addresses Coinbase's fee structure:

| Strategy Element | Fee Consideration |
|-----------------|-------------------|
| LIMIT-only execution | 0.4% maker vs 0.8% taker |
| 4h timeframe | Reduce turnover |
| Fee-multiple targets | Profit must exceed 2×-4× fees |
| Retest-first entry | Patient fills, avoid taker fees |
| Viability filter | Skip when ATR < 2× round-trip fees |
| Dynamic symbol filtering | Exclude high-spread symbols |

---

## Development Philosophy

**Production**:
- Multi-path exits (redundancy)
- Signal-based adaptation
- Maker-first execution
- Data-driven filtering

**Backtest**:
- Lower frequency wins
- Compression-to-expansion trades (volatility regime shifts)
- Patient entries (quality > quantity)
- Fee-multiple targets
- Incremental optimization (staged tuning)

---

## Critical Files

**Production**:
- `main.py` - Entry point
- `webhook/listener.py` - Order execution
- `sighook/sender.py` - Signal generation
- `MarketDataManager/position_monitor.py` - Exit decision engine

**Backtest**:
- `backtest/strategy_4h_hybrid.py` - State machine (2800+ lines)
- `backtest/engine_4h_hybrid.py` - Backtest engine
- `backtest/config_4h_hybrid.py` - Configuration presets
- `backtest/run_single_180d.py` / `run_single_360d.py` - Test runners

**Infrastructure**:
- `fifo_engine/engine.py` - FIFO P&L
- `SharedDataManager/shared_data_manager.py` - Cross-container sync
- `docker-compose.aws.yml` - Production deployment

---

## Current Status (February 4, 2026)

- **Production**: Stable, signal-based exits active, dynamic filtering enabled
- **Backtest**: Phase 2.3 ROC/compression calibration in progress
- **Recent Fixes**: Stage C warmup logging, ROC percentile NaN filtering
- **Next Steps**: A/B test Stage C compression policy, finalize ROC threshold

---

## Key Metrics Summary (180d Baseline)

| Metric | Value | Notes |
|--------|-------|-------|
| Total 4h bars | 1,078 | Processable boundaries |
| Regime OK bars | 302 (28.0%) | EMA200 regime filter |
| BASE_OK bars | 204 (18.9%) | Regime + viability |
| ROC-OK bars | 68 (6.3%) | BASE_OK + roc_score >= threshold |
| Structure-OK bars | 12 (1.1%) | ROC-OK + compression filter |
| Setups created | 12 | 100% conversion from structure_ok |
| Entries filled | 11 | 91.7% setup→entry conversion |
| State occupancy (FLAT) | 924 bars (85.7%) | ~221k minutes |
| State occupancy (IN_POSITION) | 154 bars (14.3%) | ~37k minutes |
| ROC-OK bars locked | 56/68 (82.4%) | Missed due to IN_POSITION state |
| Compressed entry trades | 5/11 (45.5%) | Win rate: 60%, Hold: 77.5h |
| Normal entry trades | 6/11 (54.5%) | Win rate: 16.7%, Hold: 40.3h |

---

## References

- **Session Plans**: `.claude/sessions/`
- **Architecture Deep Dive**: `docs/active/architecture/ARCHITECTURE_DEEP_DIVE.md`
- **A/B Test Plan**: `backtest/STAGE_C_AB_TEST_PLAN.md`
- **Deployment Guide**: `.claude/DEPLOYMENT.md`
