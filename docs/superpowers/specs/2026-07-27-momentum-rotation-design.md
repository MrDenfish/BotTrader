# Momentum Rotation Strategy — Design

**Date:** 2026-07-27
**Status:** Approved design, pre-implementation
**Supersedes:** `composite_scoring` as the production strategy (composite remains in the repo as a dormant plugin)

## 1. Motivation

The v2 bot has run a short-timeframe (5-minute bar) composite-scoring strategy in paper trading since February 2026. Two structural properties of that design motivate this redesign:

1. **Fee economics.** At ~0.65% round-trip fees, a strategy trading intraday-to-48h holds needs a per-trade gross edge that short-timeframe signals did not reliably provide. Slower strategies targeting multi-percent moves make the fee hurdle small relative to the target.
2. **Universe effects dominate timing effects.** Outcomes tracked which symbols entered the tradeable universe far more than any per-entry condition. A cross-sectional design makes symbol selection the explicit strategy instead of an uncontrolled input.

Detailed evaluation data is maintained privately and is intentionally not part of this document.

## 2. Strategy summary

Regime-gated, long-only, cross-sectional momentum rotation on daily bars.

- Rank a screened universe of liquid Kraken USD pairs by risk-adjusted momentum.
- Hold the top **K = 4** as an inverse-volatility-weighted portfolio.
- Rebalance weekly at a fixed time (Monday 00:00 UTC).
- Hold cash — fully out of the market — whenever the regime gate is closed.

## 3. Universe & data

**Universe screens** (evaluated weekly at rebalance, via the existing pair-discovery plugin with tightened config):

| Screen | Value |
|---|---|
| Quote currency | USD |
| 24h volume floor | fixed absolute floor, from pre-declared menu {$5M, $10M} (§12) |
| Max spread | ≤ 20 bps |
| Minimum listing age on Kraken | ≥ 180 days |
| Stablecoins | excluded |
| Universe size | up to 25 pairs — best-ranked by volume among those passing all screens |

**The floor is fixed, never dynamic, and never lowered to hit a universe-size target.** A shrinking universe in quiet markets is intended behavior: fewer eligible names → smaller portfolio → more cash. The screen doubles as a passive regime filter, pushing in the same direction as the regime gate (§5). The strategy remains fully functional down to ~8 eligible names; below that, unfilled slots stay in cash per §6. In backtests over multi-year history, the fixed dollar floor yields a smaller eligible universe in early low-volume eras — accepted as a conservative bias.

Universe membership changes take effect only at rebalance. A held coin leaving the universe is not force-sold intra-week; it becomes ineligible for purchase at the next rebalance.

**Data.** Signals are computed from Kraken REST daily OHLC (same API already used by the dashboard strategy probe). The 5-minute WebSocket pipeline is retained for execution, fills, and monitoring only — it is no longer a signal input. Consequences:

- No warmup blackout after restarts (daily history fetches on startup).
- Backtests can use 5+ years of daily history per symbol.

## 4. Signal & portfolio construction

**Score** per coin: trailing `L`-day return (excluding the most recent 2–3 days, to avoid buying immediate spikes) divided by daily return volatility over the same window. `L` is selected in backtest from a pre-declared menu of {30, 60, 90}; no wider sweep is permitted.

**Selection with hysteresis:** hold the top 4 by score. A current holding is retained while it stays above rank `B` (band selected from {6, 8}); it is replaced only when it falls below the band. This bounds fee-paying churn at the rank boundary.

**Expected turnover:** ~1–2 position swaps per week, frequently zero.

## 5. Regime gate

Both conditions must hold for the portfolio to be invested:

1. **Market filter:** BTC daily close above its 200-day simple moving average.
2. **Absolute-momentum floor:** a coin is only eligible while its own `L`-day return is positive (relative ranking alone would hold "least-bad" coins in a downtrend).

**Evaluation timing — fast out, slow in.** The market filter (condition 1) is evaluated **daily** after the UTC daily close; if it fails, all holdings are liquidated at that day's evaluation rather than waiting for the weekly rebalance. The absolute-momentum floor (condition 2) is a selection criterion evaluated at weekly rebalance only. New entries happen only at weekly rebalance in all cases.

Gate closed → cash. "In cash, regime off" is a first-class healthy state and must be presented as such on the dashboard (per the May 2026 idle-vs-broken lesson).

## 6. Sizing, execution & risk

**Sizing.** Inverse-volatility weights across the ≤4 holdings, per-position cap 30% of equity, scaled to a portfolio volatility target chosen in backtest so that backtested max drawdown ≤ ~12–14%. If fewer than 4 coins qualify, the remainder stays in cash — slots are never force-filled.

**Execution.** Post-only limit at the touch with a bounded chase: reprice up to N times, then cross the spread. All backtests assume the full 0.65% round-trip taker fee plus 10 bps slippage; maker fills are upside, never an assumption.

**Risk rails** (implemented in the existing risk-manager chain):

1. **Catastrophe stop:** per-position hard exit ~25% below entry. Not a trading signal — a backstop for delistings/flash events. The weekly rotation is the normal exit mechanism.
2. **Portfolio kill-switch:** at 15% equity drawdown from high-water mark, liquidate to cash and halt trading until manually re-enabled.
3. Retained plugins: `basic`, `circuit_breaker`. Not in this strategy's path: `exit_manager`, `performance_filter` (dormant, not deleted).

**Behavioral contract:** the strategy holds through ordinary dips between rebalances by design. Intra-week discretionary intervention defeats the design and invalidates the validation.

## 7. Validation protocol

- **Data:** 5+ years of Kraken daily OHLC for all USD pairs passing a relaxed screen historically, not just today's shortlist. Survivorship bias is the primary threat; the backtest selects from what was tradeable at each point in time, and residual bias is documented explicitly.
- **Structure:** walk-forward by era. Parameters (`L`, band `B`, vol target) are fitted on the oldest era, checked on middle eras, and finally run once on an untouched holdout (most recent ~18 months).
- **Pre-declared pass bar** (all required):
  - Positive net return after fees + slippage in every era, including holdout.
  - Backtested max drawdown ≤ 15%.
  - Outperforms holding cash.
  - Regime gate demonstrably avoids the major bear legs.
- Beating BTC buy-and-hold is explicitly **not** the bar.
- **Failure handling:** if the pass bar is not met, the strategy is not deployed. No post-hoc parameter rescue.

## 8. Rollout

1. Backtest gauntlet (above).
2. 8–12 weeks paper trading on the live pipeline. At weekly cadence this validates *behavior* (fills, gate transitions, tracking vs backtest expectations), not statistics.
3. Real money at small scale with the kill-switch armed.
4. Scale only after a full quarter of live behavior matching expectations.

## 9. Components to build

| Component | Kind | Notes |
|---|---|---|
| `momentum_rotation` strategy plugin | new | ranking, hysteresis, regime gate, rebalance scheduling |
| Daily-bar data plugin (REST poller) | new | daily OHLC fetch + cache; startup backfill |
| Pair-discovery config | change | tightened screens (§3); weekly evaluation cadence |
| Backtester daily-bar + portfolio support | extend | portfolio accounting, weekly cadence, era-based walk-forward harness |
| Kill-switch risk rail | extend | high-water-mark drawdown halt (extends circuit-breaker family) |
| Dashboard rotation panel | new | holdings, ranks, regime state, distance-to-kill-switch; "in cash" as healthy state |

Everything else — event bus, registry, Kraken exchange/auth, persistence, Docker/Caddy deployment — is reused unchanged.

## 10. Explicitly retired from the active path

Kept in-repo, dormant: `composite_scoring` (+ guardrails), `exit_manager`, `performance_filter`, `mean_reversion_v3`, `hybrid_4h_maker`. Historical artifacts (no further maintenance): 5-minute backtest datasets, Entry Quality dashboard page, fee-sizing analysis scripts.

## 11. Testing

- Unit tests per new component (ranking math, hysteresis, gate logic, inverse-vol sizing, kill-switch state machine), following existing v2 test conventions.
- Backtest harness tests: known-input fixture universe with hand-computed expected rebalances.
- Integration: paper-mode dry run producing a full rebalance cycle end-to-end against recorded daily data.
- Existing 704-test suite must remain green (dormant plugins keep their tests).

## 12. Open parameters (to be fixed by backtest, then frozen)

`L` ∈ {30, 60, 90} · recent-skip ∈ {2, 3} days · band `B` ∈ {6, 8} · volume floor ∈ {$5M, $10M} (one pre-registered sensitivity check; if results are robust at both, the stricter wins) · vol target (drawdown-matched) · chase count `N`. No parameters outside this menu may be introduced without restarting the validation from scratch.
