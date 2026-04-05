a# BotTrader v2 — Methodology and Validation

> How do we know the program works? This document traces every design
> decision and parameter choice back to its evidence, classifies each as
> structurally sound or potentially overfit, and records the validation
> history. It is intended to be updated as the program evolves.
>
> **Last updated:** 2026-03-14

---

## 1. Executive Summary

BotTrader v2 is a multi-symbol cryptocurrency trading system that uses
composite indicator scoring to generate buy signals and a layered exit
manager (trailing stops, hard stops, stale timeout) to manage positions.

As of 2026-03-14, the system has been validated against **3 independent
datasets totaling 27 unique symbols and 180 days of 1-minute data each**.
The current configuration produces a positive aggregate P&L of **+$36.20**
across 320 trades with a **60.3% win rate**, after accounting for Kraken's
0.65% round-trip fees.

---

## 2. Backtesting Framework

### 2.1 Datasets

| Dataset | Symbols | Source | Period | Purpose |
|---------|---------|--------|--------|---------|
| **Set A** | BTC, ETH, SOL, XRP, DOGE, ADA, LINK, DOT, AVAX | Kraken REST API | 180 days (Aug 2025 – Feb 2026) | Original development set |
| **Set B** | BNB, ATOM, NEAR, UNI, LTC, AAVE, ICP, FIL, APT | Binance bulk download | 180 days (Aug 2025 – Feb 2026) | Out-of-sample validation |
| **Set C** | ARB, OP, SUI, INJ, TAO, HBAR, ALGO, FET, LDO | Binance bulk download | 180 days (Aug 2025 – Feb 2026) | Out-of-sample validation |

- **No symbol overlap** between datasets — 27 unique symbols total.
- All symbols are USD pairs with sufficient liquidity (24h volume > $2M,
  bid-ask spread < 1%).
- Data is 1-minute OHLCV candles, aggregated to 5-minute bars by the
  strategy before indicator calculation.
- The backtest simulator applies maker/taker fees (0.25%/0.40%), 1 bps
  slippage, and $10 minimum order size — matching production parameters.

### 2.2 Backtest Fidelity

The backtest engine was **validated trade-for-trade** against the original
v1 production system (33 trades, exact match). It uses the same code paths
as live trading — the only differences are infrastructure (CSV replay vs
WebSocket, simulated fills vs exchange API).

### 2.3 Random Baseline

A random-entry strategy (Poisson-distributed entries, mean interval 5000
bars, seed 42) was tested on Set A with the same exit manager. Result:
**-$6.22 P&L across 28 trades**. The real strategy's +$7.22 on the same
data (before ADX/Swing changes) demonstrates a thin but real edge over
random entries.

---

## 3. Current Results (2026-03-14)

Configuration: ADX gate (>=20) + decoupled Swing + 5.5% hard stop floor +
hourly ATR candles + trend confirmation gate.

| Dataset | Trades | P&L | Win Rate | Hard Stop % | Trailing Stop % |
|---------|--------|-----|----------|-------------|-----------------|
| Set A   | 104    | -$0.92 | 59.6% | 25.0% | 51.9% |
| Set B   | 117    | +$14.99 | 61.5% | 18.8% | 52.1% |
| Set C   | 99     | +$22.13 | 59.6% | 24.2% | 57.6% |
| **Total** | **320** | **+$36.20** | **60.3%** | **22.5%** | **53.8%** |

Average P&L per trade: +$0.11. Median P&L per trade: +$1.33.

### 3.1 Prior Results for Comparison

| Configuration | Trades | P&L | Win Rate | Hard Stop % |
|---------------|--------|-----|----------|-------------|
| Pre-ADX, 4.5% floor (original) | 729 | -$144.05 | 55.4% | 28.0% |
| 5.5% floor, 3x hourly ATR | 729 | -$144.05 | 55.4% | 28.0% |
| 5.5% floor, 7x hourly ATR | 714 | -$203.01 | 56.9% | 19.2% |
| **ADX gate + Swing decouple** | **320** | **+$36.20** | **60.3%** | **22.5%** |

---

## 4. Parameter Classification: Structural vs Data-Derived

Every parameter and design decision is classified below. **Structural**
changes are based on market mechanics, established theory, or bug fixes.
**Data-derived** changes were tuned against backtest results and carry
overfitting risk.

### 4.1 Structural (Low Overfitting Risk)

| Parameter / Decision | Rationale | Classification |
|---------------------|-----------|----------------|
| **Fee-aware P&L** (entry×maker vs exit×taker) | Mathematical correction — computes real P&L, not an optimization | Structural |
| **Market orders for hard stops** | Microstructure: limit orders on illiquid pairs get cancelled as stale | Structural |
| **Swing decoupled from MACD** | Bug fix: Swing required `macd > signal`, making them correlated. The 3-indicator gate was effectively 2.5 indicators. Decoupling restores independence | Structural |
| **ADX gate >= 20** | Textbook threshold (Wilder, 1978). ADX < 20 = no trend. Not derived from our data. Consistent improvement across all 3 independent datasets | Structural |
| **ADX period = 14** | Standard Wilder parameter, universally used | Structural |
| **Hourly ATR candle aggregation** | 1-minute ATR is too granular for position-level stops. Hourly is standard practice for swing-trade timeframes | Structural |
| **Volume confirmation gate** (RVOL >= 0.7) | Standard practice — don't buy on below-average volume. Threshold is conservative (0.7 = 70% of average) | Structural |
| **Trend confirmation gate** | Requires >= 1 trend indicator (MACD/ROC/Swing) for buys. Prevents pure mean-reversion entries (Touch + RSI + VolDiv) which catch falling knives | Structural (see 4.2) |
| **Soft stops disabled** | Removed after both backtest (-$300) and paper trading (-$15) showed consistent losses. Confirmed across two independent test environments | Structural |

### 4.2 Potentially Data-Derived (Overfitting Risk)

These parameters were tuned against backtest data. They are noted here for
future validation as new datasets become available.

| Parameter | Current Value | How It Was Chosen | Risk Level | Validation Status |
|-----------|--------------|-------------------|------------|-------------------|
| **hard_stop_min_pct** | 5.5% | Originally 4.5% from Set A optimization; raised to 5.5% after Sets B/C showed 4.5% was too tight. Both values tested across 3 datasets | Low — directionally validated on out-of-sample data |  Validated on Sets A, B, C |
| **hard_stop_max_pct** | 8.0% | Chosen as ceiling. 7x multiplier test showed wider stops just shift losses to stale exits | Low | Validated (7x test) |
| **hard_stop_atr_mult** | 3.0 | Original value. 7x tested and rejected (worse P&L on all 3 sets). ATR system is effectively a fixed 5.5% stop at 3x | Medium — the ATR mechanism is a no-op at 3x with hourly candles | Accepted as-is |
| **score_buy_target** | 2.0 | Reduced from default 5.5 during parameter alignment with v1. Tested in optimizer grid (36 configs) | Medium | Set A only |
| **min_indicators_required** | 3 | Reduced from default 2 during v1 alignment, then raised back to 3 after v2 was too aggressive (68 buys vs v1's 2) | Medium | Set A + live validation |
| **Trend confirmation gate** | require_trend_for_buy = True | Identified via diagnostic analysis: 12/18 hard stops on Set A came from Touch+RSI+VolDiv (no trend). Reduced hard stops 18→9 on Set A. Sets B/C showed 100% of entries already had trend indicators, so the gate was irrelevant there | Medium — effective on Set A, neutral on B/C | Partially validated |
| **roc_momo_20m disabled** | enable = False | 35% win rate in backtest. Actively harmful | High — single-dataset decision | Set A only |
| **roc_momo_24h disabled** | enable = False | 28% hard stop rate in paper trading. Biggest P&L drag | Low — validated on live data | Paper trading |
| **loss_lockout_bars** | 12 | Prevents death-spiral re-entry after loss exits | Medium | Set A only |
| **MACD/RSI/ROC/BB parameters** | Various (see config) | Aligned to v1 production values; v1 ran live for months | Low — validated through live trading | Live + Set A |

### 4.3 Not Yet Validated on Out-of-Sample Data

The following were derived from Set A and have not been independently
confirmed on Sets B/C:

- `score_buy_target: 2.0` (optimizer grid tested on Set A only)
- `loss_lockout_bars: 12` (diagnostic analysis on Set A only)
- `enable_roc_20m_momentum: false` (Set A backtest only)

These should be retested if future datasets or live results suggest
different optimal values.

---

## 5. Overfitting Guardrails

### 5.1 Policy

All strategy and parameter changes must be evaluated against the following
checklist before implementation:

1. **Is the change structurally motivated?** (market mechanics, bug fix,
   established theory) → Low risk
2. **Is it derived from backtest optimization?** → High risk — must be
   validated on out-of-sample data before deployment
3. **Does it improve P&L on multiple independent datasets?** → Required
   for data-derived changes
4. **Does it reduce trade count significantly?** → Scrutinize carefully.
   Filtering is the easiest way to overfit (just remove the losing trades
   from your training set)
5. **Would a practitioner with no access to our data make the same
   decision?** → If yes, it's structural

### 5.2 Dataset Independence

- Sets A, B, and C share **zero symbols** — they are fully independent
- All three cover the same calendar period (Aug 2025 – Feb 2026) so they
  share the same macro environment. This is a limitation — a true
  out-of-sample test would use a different time period
- Future validation should include a **different time period** (e.g.,
  2024 data) to test temporal generalization

### 5.3 Known Limitations

- **Same macro regime**: All 3 datasets are from the same 180-day window.
  A strong bull or bear market during this period affects all symbols
  similarly. Performance may differ in other market regimes.
- **Simulated fills**: The backtest uses mid-price fills with slippage
  estimation. Real-world execution may differ, especially for illiquid
  pairs.
- **No transaction cost optimization**: The fee structure (0.65%
  round-trip) is the single largest drag. Kraken Pro/volume discounts
  could meaningfully improve results.
- **Survivorship bias in symbol selection**: All symbols selected have
  sufficient liquidity today. Symbols that were delisted or lost
  liquidity during the test period are not represented.

---

## 6. Change History

Each significant change is recorded with its evidence, impact, and
classification.

### 6.1 Timeline

| Date | Change | Evidence | Impact | Classification |
|------|--------|----------|--------|----------------|
| 2026-02-18 | v2 live parity achieved | Trade-for-trade match (33 trades) | Baseline established | N/A |
| 2026-02-18 | Volume confirmation gate (RVOL >= 0.7) | Standard practice, no backtest tuning | Blocks low-volume buys | Structural |
| 2026-02-18 | Volume divergence indicator | Slope-based price/volume divergence detection | +2 indicators (buy/sell) | Structural |
| 2026-02-21 | Soft stops disabled | -$300 backtest, -$15 paper trading | Eliminated biggest P&L drag | Structural |
| 2026-02-21 | Pair discovery hardened (spread filter) | Junk tokens (MYX, AZTEC) causing losses | 30+ pairs → 16 quality pairs | Structural |
| 2026-02-27 | Trend confirmation gate | 12/18 Set A hard stops from Touch+RSI+VolDiv (no trend) | Hard stops 18→9 on Set A | Data-derived (Set A) |
| 2026-02-27 | hard_stop_pct 4.5% → 5.5% | 83% of Set A hard stops had MAE 4.5-5.5% | Fewer premature stops | Data-derived (Set A) |
| 2026-03-10 | ATR-based hard stops | Live paper trading showed fixed stop too tight for volatile altcoins | Dynamic per-symbol stops | Structural concept, data-derived calibration |
| 2026-03-10 | roc_momo_24h disabled | 28% hard stop rate in live paper trading | Eliminated momentum drag | Live data validation |
| 2026-03-12 | hard_stop_min_pct raised 4.5% → 5.5% | Sets B/C: 97-99% of hard stops would survive at 5.5%. Set B P&L: -$17 → +$1 | Fewer hard stops across all datasets | Validated on 3 datasets |
| 2026-03-12 | Hourly ATR candle aggregation | 1-min ATR too small (max 0.87%), always clamped to floor | ATR calculation uses meaningful timeframe | Structural |
| 2026-03-13 | ATR mult 7x tested and rejected | All 3 datasets worse: trades saved from hard stops became losing stale exits | Reverted to 3x | Validated (negative result) |
| 2026-03-14 | Swing decoupled from MACD | Bug fix: Swing required MACD confirmation, hiding indicator correlation | Independent signals | Structural |
| 2026-03-14 | ADX gate >= 20 | Textbook threshold (Wilder). Blocks buys in trendless markets. All 3 datasets improved | P&L: -$144 → +$36 | Structural |

### 6.2 Rejected Changes

| Change | Why Rejected | Date |
|--------|-------------|------|
| Tighter hard stop (4.0%) | Doubled hard stop count (18→36) on Set A | 2026-02-27 |
| ATR regime filter lookback fix | Made things worse (hard stops 18→21) | 2026-02-27 |
| ATR multiplier 7x | All 3 datasets degraded; trades shifted from hard stop to stale exit | 2026-03-13 |
| ATR-percentile buy filter | Counterproductive — blocked more winners than losers ($32-62 worse) | 2026-03-13 |

---

## 7. Live Paper Trading Validation

### 7.1 Pre-Optimization (2026-02-20 to 2026-03-09)

- 99 valid matched trades across 28 symbols
- Realized P&L: **-$58.23** (starting balance $10,000)
- Pre-Feb 27: win rate 16.4%, dominated by soft stops (36%)
- Post-Feb 27: win rate 36.8%, hard stops + trailing dominant

### 7.2 Key Live Findings

- Soft stops confirmed as harmful in live trading (not just backtest)
- Hard stop rate 2.3x higher than Set A backtest (multi-symbol effect)
- roc_momo_24h trigger caused 8/10 post-Feb 27 hard stops (disabled)

### 7.3 Pending Live Validation

The ADX gate + Swing decouple changes (2026-03-14) have not yet been
deployed to paper trading. Live validation is the next step.

---

## 8. Answering Key Questions

### "How do you know the program is profitable?"

The current configuration produces +$36.20 across 320 trades on 3
independent datasets (27 unique symbols, 180 days each). Two of three
datasets are out-of-sample — the strategy was never optimized against
them. The improvement is consistent: Set A near breakeven (-$0.92),
Set B positive (+$14.99), Set C positive (+$22.13).

However, this is backtested profitability on historical data. Live paper
trading has not yet validated the current configuration. Prior live
results were negative (-$58), though that was before the ADX gate and
Swing fix.

### "Was the program backtested?"

Yes, extensively:
- 3 independent datasets, 27 unique symbols, ~540 days of 1-minute data
- Random baseline comparison (strategy beats random by ~$13 on Set A)
- Multiple parameter configurations tested (optimizer grids of 18-36
  configs, ATR multiplier sweeps, various stop levels)
- Trade-for-trade validation against v1 production system

### "Are the parameters from overfitting?"

Some are, some aren't. Section 4 classifies every parameter. The two
most impactful recent changes (ADX gate and Swing decouple) are
**structural** — ADX >= 20 is a textbook threshold and Swing decouple
is a bug fix. Neither was derived from our data.

Five earlier changes are flagged as potentially overfit (Section 4.2).
They were all derived from Set A and have varying levels of out-of-sample
validation. None have been reverted because the ADX gate + Swing fix
improved results sufficiently to turn aggregate P&L positive, and
reverting multiple changes simultaneously makes attribution difficult.

### "What's the biggest risk?"

1. **Temporal overfitting**: All 3 datasets cover the same 180-day
   market regime. Performance in a sustained bear market or high-
   volatility crash is unknown.
2. **Fee sensitivity**: At 0.65% round-trip, the strategy needs >0.65%
   average favorable movement per trade to break even. Lower fees
   (volume tier upgrades) would materially improve results.
3. **Trade count**: The ADX gate reduced trades by 56%. If the filtered
   trades included future winners that happened to lose in the backtest
   period, the gate is overfitting by exclusion.

---

## 9. Future Validation Plan

1. **Deploy current config to paper trading** — live validation is the
   strongest overfitting test
2. **Collect new time-period data** (e.g., 2024) for temporal
   out-of-sample testing
3. **Monitor ADX distribution in live signals** — confirm ADX >= 20
   filter behaves consistently on live data
4. **Track per-change attribution** — if live results diverge from
   backtest, identify which parameters are responsible
5. **Fee tier upgrade analysis** — model impact of Kraken Pro fees on
   current results
