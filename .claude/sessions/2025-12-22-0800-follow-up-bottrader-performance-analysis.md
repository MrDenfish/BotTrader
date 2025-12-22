# Follow-up BotTrader Performance Analysis Session
**Started:** 2025-12-22 08:00 PT (2025-12-22 16:00 UTC)

## Session Overview
This session builds on the December 21st troubleshooting session (8 bug fixes) and addresses the critical performance issues identified in `docs/analysis/PERFORMANCE_ANALYSIS_2025-12-03.md`.

---

## Context: Previous Session Bug Fixes (Dec 21, 2025)

The following 8 issues were fixed and deployed in the prior session:

| # | Issue | Fix | Commit |
|---|-------|-----|--------|
| 1 | ROC sell threshold positive instead of negative | Negate threshold in `signal_manager.py:39` | bc3b532 |
| 2 | Cron jobs using missing `.env_runtime` | Updated root crontab to use `.env` | AWS config |
| 3 | CSV reports saving to `/tmp` (ephemeral) | Changed save path to `/app/logs` | 4bb24a0 |
| 4 | Webhook container health check failing | Transient - self-recovered | N/A |
| 5 | Dynamic filter: wrong db session attribute | `db_session_manager` → `database_session_manager.async_session()` | db152ee |
| 6 | Dynamic filter: missing SQL `text()` wrapper | Added `text()` for SQLAlchemy async | 7f95e38 |
| 7 | TP_SL_LOG_PATH had local Mac path | Updated AWS `.env` to `/app/logs/tpsl.jsonl` | AWS config |
| 8 | Order manager calling wrong method | `_compute_tp_price_long` → `_compute_stop_pct_long` | 7f95e38 |

**Result:** All containers healthy, email reports working, trading capability restored.

---

## Context: Performance Analysis Summary (Dec 3, 2025)

### Critical Findings

| Metric | Current | Target | Status |
|--------|---------|--------|--------|
| Win Rate | 20.2% | >50% | CRITICAL |
| Max Drawdown | 319.7% | <30% | CRITICAL |
| Profit Factor | 0.06 | >1.5 | CRITICAL |
| Avg Win | $4.67 | - | Too small |
| Avg Loss | $21.93 | - | 4.7x larger than wins |
| Risk:Reward | 4.7:1 (inverted) | 1:2+ | CRITICAL |

### Root Cause Analysis

1. **Exit Logic Issues** - 6 different exit paths causing premature exits:
   - POSITION_MONITOR (soft/hard stops)
   - Take Profit triggers
   - Stop Loss triggers
   - Signal Reversals (Phase 5)
   - Manual exits
   - Liquidations

2. **Phase 5 Signal-Based Exits** (deployed Nov 30, 2025):
   - Exits on ANY bearish signal, even minor pullbacks
   - Likely culprit for small wins being cut short
   - Winners closed too early, losers allowed to run

3. **Stop Loss Configuration**:
   - HARD_STOP at -5% may be too tight for crypto volatility
   - SOFT_STOP at -2.5% triggering premature exits

### Analysis Recommendations

From `PERFORMANCE_ANALYSIS_2025-12-03.md`:

1. **Disable Phase 5 signal-based exits** - Test without signal reversals
2. **Widen stop losses** - HARD_STOP to 7-10%, disable SOFT_STOP temporarily
3. **Verify emergency stops** - Ensure -15% hard floor is active
4. **Adjust take profit** - Let winners run longer
5. **Add signal confirmation** - Require multiple confirming signals before exit
6. **Implement trailing stops** - Lock in profits without premature exit

---

## Goals

- [ ] Review current exit logic implementation across all 6 paths
- [ ] Evaluate Phase 5 signal-based exit impact on win rate
- [ ] Analyze stop loss configuration (HARD_STOP/SOFT_STOP thresholds)
- [ ] Review take profit logic and R:R ratios
- [ ] Identify quick wins vs. structural changes needed
- [ ] Create implementation plan for recommended fixes
- [ ] Test changes in isolation before AWS deployment

---

## Key Files to Review

| File | Purpose |
|------|---------|
| `webhook/webhook_order_manager.py` | Take profit, stop loss, exit logic |
| `sighook/signal_manager.py` | Signal generation, Phase 5 exits |
| `sighook/position_monitor.py` | SOFT_STOP, HARD_STOP monitoring |
| `config/bot_config.py` | Threshold configurations |
| `.env` | Runtime threshold values |

---

## Progress Log

- **08:00 PT** - Session started, context established from prior fixes and performance analysis
- **08:15 PT** - Completed code review of exit logic implementation
- **08:30 PT** - Analyzed performance data, found P&L not being recorded since Dec 21
- **08:40 PT** - Fixed FIFO script permissions (was 700, now 755)
- **08:45 PT** - Ran FIFO computation, verified P&L in fifo_allocations table

---

## Performance Analysis Results

### FIFO Cron Job Issue (Fixed)

**Problem:** `/app/scripts/` directory had `drwx------` (700) permissions - only root could read.
The cron job was failing silently:
```bash
*/5 * * * * docker exec sighook python3 -m scripts.compute_allocations --version 2 --all-symbols
# Error: No module named scripts.compute_allocations
```

**Fix Applied:**
```bash
docker exec -u root sighook chmod -R 755 /app/scripts
```

**Note:** This fix is temporary - the container will revert on rebuild. Need to fix Dockerfile permissions.

---

### Performance Comparison (Using FIFO Data)

| Metric | Pre-Phase 1 (Dec 1-15) | Post-Phase 1 (Dec 21-22) | Change |
|--------|------------------------|--------------------------|--------|
| **Trades** | 333 | 9 | Small sample |
| **Win Rate** | 24.3% | 44.4% | **+20pp** |
| **Avg Win** | $0.67 | $0.14 | -79% |
| **Avg Loss** | -$1.63 | -$2.40 | -47% worse |
| **Profit Factor** | 0.132 | 0.047 | -64% worse |
| **Total P&L** | -$355.97 | -$11.42 | - |

### Individual Trade Breakdown (Dec 21-22)

| Symbol | Size | Cost Basis | Sell Price | P&L | Result | Note |
|--------|------|------------|------------|-----|--------|------|
| ZKP-USD | 195.0 | $0.1540 | $0.1544 | +$0.01 | WIN | |
| AVNT-USD | 106.6 | $0.3663 | $0.2821 | -$9.05 | LOSS | Old position liquidated |
| MLN-USD | 6.3 | $4.9533 | $4.7800 | -$1.17 | LOSS | Old position liquidated |
| ZKP-USD | 181.8 | $0.1657 | $0.1598 | -$1.16 | LOSS | |
| ZKP-USD | 157.2 | $0.1917 | $0.1894 | -$0.44 | LOSS | |
| ZKP-USD | 160.6 | $0.1888 | $0.1910 | +$0.19 | WIN | |
| ZKP-USD | 0.2 | $0.1888 | $0.1958 | +$0.00 | WIN | Dust |
| ZKP-USD | 158.4 | $0.1906 | $0.1899 | -$0.17 | LOSS | |
| ZKP-USD | 157.7 | $0.1911 | $0.1943 | +$0.36 | WIN | |

### Key Observations

1. **Win Rate Improved**: 24% → 44% (Phase 1 working partially)

2. **R:R Still Inverted**: Losses still larger than wins
   - Avg win: $0.14
   - Avg loss: -$2.40
   - This is **17x worse** (should be inverted)

3. **Legacy Positions Hurting P&L**:
   - AVNT-USD sold at $0.28 vs cost basis $0.37 = -$9.05 loss
   - MLN-USD sold at $4.78 vs cost basis $4.95 = -$1.17 loss
   - These are old positions being liquidated, not new trade strategy

4. **Trailing Stops Not Activating**:
   - `TRAILING_ACTIVATION_PCT=0.035` (3.5%)
   - Most trades are closing with <1% moves
   - Need longer holds or lower activation threshold

5. **Phase 5 Disabled Helps**: No premature signal exits cutting winners

---

### Recommendations

**Immediate:**
1. Fix Dockerfile to set correct permissions on /app/scripts
2. Lower trailing stop activation to 1.5-2%
3. Review legacy positions and consider closing at better prices

**Short-term:**
1. Increase position hold times (trailing stop should do this)
2. Consider wider entry requirements to reduce trade frequency
3. Monitor for 7+ days with current settings for statistical significance

---

## Code Review Findings

### Exit Logic Architecture (6 Paths)

| Path | File | Trigger Condition | Current Config |
|------|------|-------------------|----------------|
| 1. HARD_STOP | `position_monitor.py:245` | P&L ≤ -5% | `HARD_STOP_PCT=0.05` |
| 2. SOFT_STOP | `position_monitor.py:251` | P&L ≤ -2.5% | `MAX_LOSS_PCT=0.025` |
| 3. SIGNAL_EXIT (Phase 5) | `position_monitor.py:314-317` | SELL signal + P&L ≥ 0% | `SIGNAL_EXIT_ENABLED=true` |
| 4. TRAILING_STOP | `position_monitor.py:293-310` | Price ≤ trailing stop | `TRAILING_STOP_ENABLED=false` |
| 5. TAKE_PROFIT | `position_monitor.py:320-338` | P&L ≥ +3.5% (no trailing) | `MIN_PROFIT_PCT=0.035` |
| 6. BRACKET_TP/SL | `webhook_order_manager.py:744-788` | Exchange-side orders | ATR-based or fixed |

### Critical Issue: Phase 5 Signal Exit Logic

```python
# position_monitor.py:314-317
elif self.signal_exit_enabled:
    current_signal = self._get_current_signal(symbol)
    if current_signal == 'sell' and pnl_pct >= self.signal_exit_min_profit:
        exit_reason = f"SIGNAL_EXIT (P&L: {pnl_pct:.2%}, signal=SELL)"
```

**Problem:** Exits on ANY sell signal when barely profitable (P&L ≥ 0%). This:
- Cuts winners short at breakeven or tiny profit
- Triggers on minor pullbacks, not trend reversals
- Explains $4.67 avg win vs $21.93 avg loss (4.7x inverted R:R)

### Stop Loss Configuration

| Parameter | Current | Effect |
|-----------|---------|--------|
| `HARD_STOP_PCT` | 5% | Emergency floor - reasonable |
| `MAX_LOSS_PCT` (soft) | 2.5% | **Too tight** - triggers on normal volatility |
| `SIGNAL_EXIT_MIN_PROFIT_PCT` | 0% | **Too aggressive** - exits at breakeven |
| `TRAILING_ACTIVATION_PCT` | 3.5% | Never activated (trailing disabled) |

### Trailing Stop Status

```python
# position_monitor.py:54
self.trailing_enabled = os.getenv('TRAILING_STOP_ENABLED', 'false').lower() == 'true'
```

**Finding:** Trailing stops are **disabled** by default. When enabled:
- Activates at +3.5% profit
- Uses 2×ATR distance with 1-2% constraints
- **Ignores signal exits** once active (good behavior)

---

## Prioritized Implementation Plan

### Phase 1: Quick Wins (Config-Only Changes) - ✅ ALREADY DEPLOYED

**Impact: HIGH | Risk: LOW | Effort: Minutes**

**Status: COMPLETE** - These settings are already live on AWS:

| # | Change | AWS Value | Status |
|---|--------|-----------|--------|
| 1.1 | **Disable Phase 5** | `SIGNAL_EXIT_ENABLED=false` | ✅ Deployed |
| 1.2 | **Enable Trailing Stops** | `TRAILING_STOP_ENABLED=true` | ✅ Deployed |
| 1.3 | **Widen Soft Stop** | `MAX_LOSS_PCT=0.045` | ✅ Deployed (4.5%) |
| 1.4 | **Raise Take Profit** | `MIN_PROFIT_PCT=0.035` | N/A (trailing active) |

---

### Phase 2: Signal Exit Improvements (Code Changes)

**Impact: HIGH | Risk: MEDIUM | Effort: Hours**

If Phase 5 signal exits are re-enabled, they need safeguards.

| # | Change | File | Description |
|---|--------|------|-------------|
| 2.1 | **Minimum profit threshold** | `position_monitor.py` | Require P&L ≥ +1.5% before signal exit |
| 2.2 | **Signal confirmation** | `position_monitor.py` | Require 2+ consecutive SELL signals |
| 2.3 | **Cooldown after entry** | `position_monitor.py` | No signal exits within first 30 minutes |
| 2.4 | **ATR-based threshold** | `position_monitor.py` | Scale exit threshold with volatility |

**Example Implementation (2.1):**
```python
# Change SIGNAL_EXIT_MIN_PROFIT_PCT from 0.0 to 0.015 (1.5%)
self.signal_exit_min_profit = Decimal(os.getenv('SIGNAL_EXIT_MIN_PROFIT_PCT', '0.015'))
```

---

### Phase 3: Stop Loss Optimization (Code Changes)

**Impact: MEDIUM | Risk: MEDIUM | Effort: Hours**

| # | Change | File | Description |
|---|--------|------|-------------|
| 3.1 | **ATR-based soft stop** | `position_monitor.py` | Replace fixed % with 1.5×ATR |
| 3.2 | **Time-based stop widening** | `position_monitor.py` | Wider stops in first hour |
| 3.3 | **Volatility regime detection** | `position_monitor.py` | Tighter stops in low-vol, wider in high-vol |

**Rationale:** Fixed % stops don't adapt to market conditions. A 2.5% move is noise in high-volatility crypto but significant in low-vol periods.

---

### Phase 4: R:R Ratio Improvements (Code Changes)

**Impact: HIGH | Risk: MEDIUM | Effort: Days**

| # | Change | File | Description |
|---|--------|------|-------------|
| 4.1 | **Asymmetric TP/SL** | `webhook_order_manager.py` | TP at 2×SL distance minimum |
| 4.2 | **Partial profit taking** | `position_monitor.py` | Sell 50% at +3%, let rest run |
| 4.3 | **Trailing stop improvements** | `position_monitor.py` | Tighter trail after significant gains |

**Target R:R:**
- Current: Losses 4.7× larger than wins
- Target: Wins 2× larger than losses (1:2 R:R)

---

### Phase 5: Entry Quality Improvements (Future)

**Impact: MEDIUM | Risk: LOW | Effort: Days**

| # | Change | File | Description |
|---|--------|------|-------------|
| 5.1 | **Higher score threshold** | `signal_manager.py` | Raise `SCORE_BUY_TARGET` from 5.5 to 6.5 |
| 5.2 | **Volume confirmation** | `signal_manager.py` | Require above-average volume on entry |
| 5.3 | **Trend alignment** | `signal_manager.py` | Only buy in uptrends (higher timeframe) |

---

## Implementation Priority Matrix

```
                    IMPACT
                HIGH        MEDIUM      LOW
         ┌──────────────────────────────────┐
    LOW  │  Phase 1     │            │      │
         │  (Config)    │            │      │
RISK     ├──────────────┼────────────┼──────┤
  MEDIUM │  Phase 2,3   │  Phase 4   │      │
         │  (Signals,   │  (R:R)     │      │
         │   Stops)     │            │      │
         ├──────────────┼────────────┼──────┤
    HIGH │              │            │      │
         └──────────────────────────────────┘
```

**Recommended Order:**
1. **Phase 1** - Immediate (config only, easy rollback)
2. **Phase 2.1** - If re-enabling signal exits
3. **Phase 3.1** - ATR-based stops
4. **Phase 4** - After baseline metrics improve

---

## Success Metrics

After implementing Phase 1, monitor for 7 days:

| Metric | Current | Target (7 days) | Target (30 days) |
|--------|---------|-----------------|------------------|
| Win Rate | 20.2% | >35% | >45% |
| Avg Win | $4.67 | >$10 | >$15 |
| Avg Loss | $21.93 | <$15 | <$10 |
| Profit Factor | 0.06 | >0.5 | >1.2 |
| Max Drawdown | 319% | <100% | <50% |

---

## Rollback Plan

If Phase 1 changes cause issues:

```bash
# Revert to previous config
ssh bottrader-aws 'cd /opt/bot && git checkout HEAD~1 -- .env'
ssh bottrader-aws 'cd /opt/bot && docker compose -f docker-compose.aws.yml restart sighook webhook'
```

---

## Notes

- Trading was restored on Dec 21 after ROC threshold fix
- Performance issues predate the Dec 21 bug fixes
- Need to distinguish between bugs vs. strategy/configuration issues
- Any changes should be testable in isolation before production deployment
- **Phase 1 changes are reversible via env vars - low risk to try immediately**
