# Regime-Gated Majors Carry — Design

**Date:** 2026-07-28
**Status:** Approved design, pre-implementation
**Relation to prior work:** Successor candidate to `composite_scoring` (retired from active development) and to the momentum rotation design (validated infrastructure retained; strategy not deployed per its spec §7). Reuses the `backtest/rotation/` gauntlet and `momentum_rotation/core.py` math.

## 1. Motivation

Two strategy families that extracted return from *predicting price* have been tested to completion and rejected. This design extracts return from three structural sources instead: (1) conditional ownership of the most liquid assets — market beta taken only when a long-horizon regime condition holds; (2) staking yield on the sleeves that pay it (live only; see §7 honesty rules); (3) avoided catastrophe — the regime gate mechanism is the single component that has passed long-history validation in this project. The only quasi-predictive element retained is that gate.

Detailed evaluation history is maintained privately and is intentionally not part of this document.

## 2. Strategy summary

Long-only, daily-gated, weekly-rebalanced holding of a fixed three-asset universe.

- **Universe (fixed):** BTC-USD, ETH-USD, SOL-USD. No discovery, no screens, no rotation.
- **Master gate:** BTC daily close > BTC 200-day SMA. Closed → entire book to cash.
- **Per-asset gate:** an asset is held only while its own close > its own 200-day SMA (protects a sleeve against a private bear while BTC remains healthy).
- **Cadence:** gates evaluated daily after UTC close ("fast out"); entries and weight changes only at the Monday rebalance from Sunday's completed bar ("slow in").

## 3. Allocation

- Among assets passing both gates: inverse-volatility weights (60-day daily vol) — or equal weights; the choice is the **entire pre-registered parameter menu** (§8) — with a **50% per-sleeve cap**, all scaled by the calibrated exposure scalar (§5).
- **Vacant sleeves stay in cash.** Weight freed by a gated-out asset is never redistributed to survivors (redistribution concentrates exactly when diversification is thinnest).
- **Drift band:** at the weekly rebalance, a sleeve trades only if its weight deviates from target by more than 20% of target. Expected turnover: a handful of gate transitions per year plus rare drift trades; most weeks are no-ops.
- Long fully-in-cash stretches are intended behavior. "In cash, gate closed" is a first-class healthy state on the dashboard.

## 4. Gates — evaluation details

- Both layers use a 200-day simple moving average of daily closes, **fixed, not swept** (long-validated module; widening the menu invites overfit).
- Insufficient history (< 200 bars) for any layer = that layer is closed (risk-off default).
- Exits triggered by either gate execute at the close **one trading day after** the signal in all backtests — a deliberate handicap modeling the manual unstake step (§6). Live operation may beat this; the backtest must not assume it.

## 5. Sizing, execution, risk

- **Exposure scalar:** the single fitted quantity. Calibrated on the fit era only (§8) to bring backtested max drawdown into the 12–14% window, then frozen into the phase lock and carried unchanged to validate and live.
- **Execution:** post-only limit at the touch, bounded chase, cross after N=3 reprices (operational constant, not a validated parameter). Zero urgency by design. Backtests charge the full 0.65% round trip + 10 bps slippage; maker economics are upside only.
- **Risk rails** (existing risk-manager chain):
  1. Portfolio kill-switch: liquidate + halt at 15% equity drawdown from high-water mark; manual re-enable.
  2. Per-position catastrophe stop 25% below entry (flash-gap backstop; a gate should always fire first).
  3. `basic` + `circuit_breaker` retained; `exit_manager` and `performance_filter` are not in this strategy's path — the gates are the exit logic.

## 6. Staking (v1 = manual, modeled honestly)

- Live real-money operation stakes the ETH and SOL sleeves via Kraken **flexible** Earn only (bonded/lockup products are incompatible with the daily fast-out and are excluded categorically).
- v1 does NOT integrate the Earn API. On gate transitions the bot fires an operator alert ("stake"/"unstake"); the ~few-per-year manual actions are acceptable, and the 1-day exit lag (§4) already prices the workflow into validation.
- Paper phase simulates yield as a daily accrual on the ETH/SOL sleeves at logged current flexible rates, reported as a **separate P&L line**, never mixed into price P&L.

## 7. Validation protocol

**Honesty rules (bind every run):**
- **No staking yield in any backtest.** Historical yield curves are unreconstructable (product launches, a multi-year US suspension); any curve would be fiction. The pass bar is price-only; yield only ever improves live results relative to validated expectations.
- 1-day exit lag applied throughout (§4).
- SOL (and early ETH) participate only where their data exists; earlier periods run the reduced book naturally.

**Era structure — pre-registered, with a disclosure:**
- **Fit:** 2017-01-01 → 2025-01-25. Rationale for the 2017 start (registered before any backtest of this design): the pre-2017 market was single-asset and structurally unlike the present, and two of the three universe assets did not exist; calibrating exposure against it would calibrate a different strategy. The fit era must still contain the 2018 and 2022 bear markets.
- **Validate:** 2025-01-26 → present. **Disclosed contamination:** this window's character was observed during momentum-rotation testing. It is acceptable as a validate era for a different return family (conditional ownership vs. momentum prediction); it can never be called a holdout for anything.
- **True holdout: forward paper trading** (§9). No historical window qualifies.

**Pass bar (all required, registered in advance):**
1. Net return > 0 after full costs in fit AND validate.
2. Max drawdown ≤ 15% in both eras.
3. Bear-leg avoidance demonstrated on the fit equity curve for 2018 and 2022 (manual criterion, per-era equity CSVs persisted).
4. Failure → not deployed, no post-hoc rescue. Same rule as every prior gauntlet.

## 8. Pre-registered parameter menu

| Parameter | Value(s) |
|---|---|
| Weight scheme | **{equal, inverse-vol(60d)}** — the entire menu (2 configs) |
| MA length (both gate layers) | 200 (fixed) |
| Per-sleeve cap | 0.50 (fixed) |
| Drift band | 20% of target (fixed) |
| Exposure | continuous, drawdown-matched to 12–14% fit-era max DD |
| Costs | 0.325% fee + 5 bps slippage per side (fixed) |

Winner: higher fit-era net among drawdown-eligible configs. No parameter outside this table may be introduced without restarting validation.

## 9. Rollout

1. Gauntlet (fit + validate, two configs, phase-locked as in the rotation runner).
2. If passed: deploy as a **new paper service alongside `v2-kraken`** (the composite bot keeps running as a regime instrument; separate accounting and attribution). Verify EC2 memory headroom before adding the container.
3. Paper phase = the true holdout, minimum one full quarter. Behavioral pass criteria: equity within the backtest's expectation corridor; gates fire on the correct days; yield accrual and alerts function; no manual intervention required beyond rehearsed steps.
4. Real money at small size, manual staking, kill-switch armed. Scale only after a further quarter of live behavior matching paper.

## 10. Components to build

| Component | Kind | Notes |
|---|---|---|
| `regime_carry` strategy plugin | new | per-asset gates + sleeve allocation; reuses `market_filter` from `momentum_rotation/core.py` |
| Engine extension: exit-lag parameter + fixed-date era bounds + 2-config menu variant | extend | `backtest/rotation/` |
| Live daily-bar REST poller plugin | new | resurrected scope from the cancelled rotation live phase |
| Yield-accrual + gate-alert observer | new | paper simulation line + operator notifications |
| Dashboard carry card | new | sleeves, gate states, yield line, distance-to-kill-switch; "in cash" styled healthy |
| Backtest↔live rebalance-convention reconciliation | task | resolve the Monday-close vs Sunday-bar convention identically in both paths (known deferred item from rotation review) |

Everything else — event bus, registry, Kraken exchange/auth, persistence, Docker/Caddy, dashboard shell — reused unchanged.

## 11. Explicitly out of scope for v1

Kraken Earn API automation; the majors dip-buying overlay (separate future experiment with its own gauntlet); any change to the running `composite_scoring` paper service; short exposure; assets beyond the fixed three.

## 12. Testing

- Unit tests: per-asset gate logic (both layers, insufficient-history default-closed), sleeve allocation with cap + no-redistribution rule, drift-band trade suppression, exit-lag application.
- Hand-computed numeric fixture: one gate-close → lagged-exit sequence with exact equity assertions (arithmetic in comments, per the rotation precedent).
- Gauntlet-variant tests: fixed-date era bounds, 2-config menu closure, phase-lock behavior.
- Existing suite (761 tests) stays green.
