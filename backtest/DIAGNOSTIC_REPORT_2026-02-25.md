# Diagnostic Backtest Report — 2026-02-25

**Config:** `v2/backtest_diagnostic.yaml`
**Data:** 180 days, 9 symbols (1-minute bars from `backtest/data/`)
**Strategy:** composite_scoring (score_buy_target=3.5, score_sell_target=3.5)

## Bug Fix Applied This Run

**Circuit breaker wall-clock time bug** — The circuit breaker used `time.time()` for
loss tracking, trip timing, cooldown checks, and loss window pruning. In backtest mode,
thousands of bars replay in seconds, so all losses appeared simultaneous, tripping the
breaker immediately and blocking all trading for the rest of the run. Fixed to use
simulated time from ticker events (same pattern as exit_manager).

## Top-Line Results

| Metric | Value |
|--------|-------|
| Total Trades | 830 (340W / 490L) |
| Gross P&L | -$23.40 |
| Total Fees | $213.92 |
| **Net P&L** | **-$237.31** |
| Win Rate | 41.0% |
| Profit Factor | 0.57 |
| Avg Win | $0.92 |
| Avg Loss | -$1.12 |
| Avg Hold Time | 12.2 hours |
| Max Drawdown | 2.44% |
| Buy Signals | 1,686 |
| Sell Signals | 4,870 |
| Buy Fills | 836 |
| Sell Fills | 830 |
| Vetoes | 4,868 |

**Key insight:** Gross P&L is only -$23.40 but fees are $213.92. **Fees account for
91% of the net loss.** The strategy has near-zero edge before costs, and fees push it
clearly negative.

## Per-Symbol Breakdown

| Symbol | Trades | Wins | Win% | Net P&L |
|--------|--------|------|------|---------|
| ADA-USD | 106 | 55 | 51.9% | +$2.60 |
| AVAX-USD | 95 | 50 | 52.6% | -$5.29 |
| BTC-USD | 66 | 31 | 47.0% | -$5.72 |
| DOGE-USD | 105 | 51 | 48.6% | -$2.01 |
| DOT-USD | 100 | 46 | 46.0% | -$13.18 |
| ETH-USD | 86 | 43 | 50.0% | -$15.96 |
| LINK-USD | 91 | 46 | 50.5% | +$9.76 |
| SOL-USD | 103 | 54 | 52.4% | +$6.43 |
| XRP-USD | 78 | 38 | 48.7% | -$0.04 |

**Observations:**
- ETH-USD and DOT-USD are the worst performers (both in sustained downtrends over the window)
- ADA-USD, LINK-USD, and SOL-USD show slight positive edges
- Win rates cluster tightly around 48-53% — the strategy is near coin-flip on direction

## Exit Reason Analysis

| Exit Reason | Trades | Net P&L | Avg P&L |
|-------------|--------|---------|---------|
| soft_stop | 256 | **-$300.59** | -$1.17 |
| hard_stop | 11 | -$71.93 | -$6.54 |
| roc_momo_20m | 19 | -$5.02 | -$0.26 |
| peak_time_limit | 7 | +$1.20 | +$0.17 |
| stale_exit (48h) | 20 | +$8.68 | +$0.43 |
| roc_momo_24h | 139 | +$14.51 | +$0.10 |
| score (signal) | 240 | +$86.16 | +$0.36 |
| trailing_stop | 138 | **+$243.59** | +$1.77 |

**Key finding:** Soft stops are responsible for **-$300.59** (31% of all trades).
Trailing stops are the only consistently profitable exit at **+$243.59**. The strategy
makes money when it lets winners run and loses money when the 3% soft stop fires.

## Entry Trigger Analysis

| Trigger | Trades | Net P&L | Win% |
|---------|--------|---------|------|
| score | 701 | -$22.45 | 51.1% |
| roc_momo_24h | 89 | +$1.71 | 47.2% |
| roc_momo_20m | 40 | -$2.66 | 35.0% |

**Finding:** `roc_momo_20m` entries have a 35% win rate — worse than random. These
entries are actively harmful and should be disabled or filtered more aggressively.

## Market Regime Analysis

| Regime | Trades | Net P&L | Win% |
|--------|--------|---------|------|
| downtrend_high_vol | 469 | **-$45.65** | 49.5% |
| uptrend_high_vol | 163 | +$17.58 | 49.1% |
| downtrend_low_vol | 127 | -$0.03 | 53.5% |
| uptrend_low_vol | 71 | +$4.70 | 47.9% |

**Finding:** 56% of all trades occur in `downtrend_high_vol` — the worst regime.
Filtering these would cut 469 trades, eliminate -$45.65 in losses, and reduce fee drag
by ~$120. A simple SMA slope filter would accomplish this.

## MFE/MAE Analysis (Stop Tuning)

| Metric | All Trades | Winners | Losers |
|--------|-----------|---------|--------|
| MFE avg (best unrealized) | 2.00% | 2.91% | 1.09% |
| MFE median | 1.43% | — | — |
| MAE avg (worst unrealized) | -1.62% | -0.69% | -2.55% |
| MAE median | -1.44% | — | — |
| Post-exit favorable avg | 0.71% | — | — |
| Post-exit favorable median | 0.45% | — | — |

**Finding:** Losing trades average 1.09% MFE — they saw profit before the stop hit.
This suggests the 3% soft stop is catching trades that were briefly profitable but
reversed. The stops may be appropriately placed (MAE for losers averages -2.55%) but
the entries are poorly timed — entering too early in moves that haven't confirmed.

## Indicator Correlation Matrix (Top Pairs)

| Pair | Correlation |
|------|-------------|
| Buy RSI / Sell Swing | r=0.903 |
| Buy Touch / Sell Swing | r=0.888 |
| Buy RSI / Buy Touch | r=0.861 |
| Buy MACD / Buy Swing | r=0.798 |
| Buy RSI / Buy Swing | r=-0.682 |

**Finding:** RSI, Touch, and Swing are highly correlated (r>0.86). These three
indicators fire together, meaning they contribute redundant information to the
composite score. The effective score is lower than it appears because 3 "independent"
indicators are really 1 signal counted 3 times.

## Recommendations (Priority Order)

### 1. Reduce Fee Drag (Biggest Impact)
- Increase position size to amortize fixed fee % over larger P&L
- Alternatively, increase score_buy_target to 4.0-4.5 to trade less frequently
  with higher conviction (current 3.5 generates too many marginal entries)

### 2. Add Regime Filter
- Skip entries when SMA slope is negative AND ATR percentile > 60
- Would eliminate ~56% of trades and most of the losses
- Simple to implement: check `sma_slope > 0` or `atr_percentile < 60`

### 3. Disable roc_momo_20m Entries
- 35% win rate is actively harmful
- Either disable as entry trigger or add a minimum threshold

### 4. Widen Soft Stop or Make It Conditional
- Soft stop at 3% fires on 31% of trades and loses $300
- Consider: widen to 4%, or only use soft stop in high-vol regimes
- In low-vol regimes, let the trailing stop do the work

### 5. De-duplicate Correlated Indicators
- RSI + Touch + Swing are measuring the same thing
- Replace with a single composite or weight them as 1 indicator in scoring

## Files

- **Config:** `v2/backtest_diagnostic.yaml`
- **Raw data:** `backtest/diagnostic_output/diagnostic_trades.jsonl` (830 trades)
- **Analysis script:** `scripts/analyze_diagnostics.py`
- **Circuit breaker fix:** `v2/plugins/risk/circuit_breaker.py`
