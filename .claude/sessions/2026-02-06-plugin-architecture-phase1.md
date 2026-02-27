# Session: Plugin Architecture Phase 1 - Foundation + 4h Hybrid Decomposition

**Started**: 2026-02-06
**Ended**: 2026-02-06
**Branch**: `refactor/plugin-architecture` (merged to `main`)
**Status**: COMPLETE

---

## Session Summary

Implemented Phase 1 of the Plugin Architecture plan: created the `strategies/` top-level package with core abstractions (StrategyPlugin ABC, types, engine interface, registry), decomposed the monolithic 2,813-line `backtest/strategy_4h_hybrid.py` into 7 focused modules, built a unified backtest engine, validated trade-for-trade exact match (8/8 trades), and deployed to AWS.

---

## Git Summary

**Commits**: 1 (commit `f3ee1f7`)
**Files Changed**: 21 files added, 0 modified, 0 deleted
**Total Lines**: +2,888 insertions

### Files Created
| File | Lines | Purpose |
|------|-------|---------|
| `strategies/__init__.py` | 30 | Package root; adds backtest/ to sys.path for bare import compatibility |
| `strategies/__main__.py` | 110 | CLI entry point (`python -m strategies`) |
| `strategies/core/__init__.py` | 1 | Core package init |
| `strategies/core/base.py` | 138 | StrategyPlugin ABC |
| `strategies/core/engine.py` | 43 | StrategyEngine ABC |
| `strategies/core/types.py` | 114 | OHLCV, StrategySignal, SignalDirection, FillNotification, StrategyMetrics |
| `strategies/registry.py` | 100 | Auto-discovery registry (singleton) |
| `strategies/hybrid_4h_maker/__init__.py` | 18 | Strategy package init |
| `strategies/hybrid_4h_maker/strategy.py` | 431 | Hybrid4hMakerStrategy(StrategyPlugin) wrapper |
| `strategies/hybrid_4h_maker/state_machine.py` | 441 | 5-state dispatch handlers |
| `strategies/hybrid_4h_maker/entry_logic.py` | 123 | Retest/chase entry logic |
| `strategies/hybrid_4h_maker/exit_logic.py` | 315 | TP/trail/stop management |
| `strategies/hybrid_4h_maker/filters.py` | 204 | Regime/viability/compression filters |
| `strategies/hybrid_4h_maker/diagnostics.py` | 318 | Gate audit, enhanced diagnostics |
| `strategies/hybrid_4h_maker/config.py` | 32 | Re-exports from backtest.config_4h_hybrid |
| `strategies/engines/__init__.py` | 5 | Engines package init |
| `strategies/engines/backtest_engine.py` | 236 | Unified BacktestEngine driving any StrategyPlugin |
| `strategies/engines/data_providers.py` | 184 | CSV loading, resampling, indicator calculation |
| `strategies/engines/fill_simulator.py` | 38 | Post-only fill simulation rules |
| `strategies/composite_scoring/__init__.py` | 6 | Phase 2 stub |
| `strategies/optimizer/__init__.py` | 1 | Phase 3 stub |

---

## Key Accomplishments

1. **StrategyPlugin ABC** - Clean interface accommodating both stateful (4h hybrid 5-state machine) and stateless (composite scoring) strategies
2. **Monolith decomposition** - 2,813-line `strategy_4h_hybrid.py` split into 7 focused modules while preserving exact behavior
3. **Unified backtest engine** - Generic engine that drives any StrategyPlugin with OHLCV dataclass bar format
4. **Auto-discovery registry** - Strategies found by `NAME` class attribute from known subpackages
5. **Trade-for-trade validation** - 8/8 trades matched exactly between old and new engines (ROC baseline config, 180d BTC-USD)
6. **CLI** - `python -m strategies --strategy hybrid_4h_maker --symbol BTC-USD --days 360 --config phase2_3`

---

## Problems Encountered and Solutions

### 1. StrategyState Enum Identity Mismatch (KeyError)
- **Problem**: `strategy_4h_hybrid.py` imported via two paths: bare `strategy_4h_hybrid` (via sys.path) and package `backtest.strategy_4h_hybrid`. Python treats these as different modules, creating non-identical enum instances.
- **Symptom**: `self.state_occupancy[current_state]` raised KeyError because StrategyState from one import path didn't match the other.
- **Fix**: Standardized ALL imports to use `from backtest.strategy_4h_hybrid import ...` consistently. Added `backtest/` to sys.path in `strategies/__init__.py` because backtest modules use internal bare imports.

### 2. ModuleNotFoundError: No module named 'config_4h_hybrid'
- **Problem**: When importing `backtest.strategy_4h_hybrid` as a package, its internal bare import `from config_4h_hybrid import Hybrid4hConfig` failed.
- **Fix**: Added `backtest/` to `sys.path` once in `strategies/__init__.py`.

### 3. Default Config Produces 0 Trades
- **Problem**: Default `Hybrid4hConfig` has very strict viability filter (maker_fee=0.6%, vol_min_mult=2.0), producing 0 trades on 60-day data.
- **Fix**: Used `get_phase2_3_roc_baseline_config()` (maker_fee=0.4%, vol_min_mult=1.0) for validation, which produces 8 trades on 180 days.

---

## Deployment

- Pushed to `refactor/plugin-architecture` branch
- Created PR #2 to `main`
- Merged PR #2
- Deployed to AWS via `ssh bottrader-aws "cd /opt/bot && git pull origin main"`
- Fast-forwarded from `a0c645a..f2f4c2d`

---

## Design Decisions

1. **`evaluate()` returns Optional[Signal], not a list** - One signal per bar per symbol
2. **Engine owns fill simulation, strategy owns decision logic** - Same strategy works in backtest and production
3. **Indicators pre-computed by engine** - Matches both existing systems
4. **State machine stays inside strategy** - 5-state machine is strategy-specific, not framework
5. **Config is strategy-specific** - No universal config schema (60+ params vs ~30 different params)
6. **Original files untouched** - `backtest/strategy_4h_hybrid.py` preserved as reference implementation

---

## What Wasn't Completed (Future Phases)

- **Phase 2**: Composite scoring strategy as plugin (extract from `sighook/signal_manager.py`)
- **Phase 3**: Unified optimizer (generalize from `optimizer_4h_hybrid.py`)
- **Phase 4**: Production integration with feature flag
- **Phase 5**: Multi-strategy running simultaneously

---

## Tips for Future Development

- The `backtest/` bare import issue is fragile. If adding new strategy packages that import from `backtest/`, ensure `strategies/__init__.py` is imported first (it puts `backtest/` on sys.path).
- When decomposing the composite scoring strategy (Phase 2), note that it's fundamentally different: stateless per-iteration scoring vs the 4h hybrid's stateful 5-state machine. The StrategyPlugin ABC accommodates both patterns.
- The validation approach (run old engine → run new engine → compare trade-for-trade) was essential for catching the enum identity bug. Use the same approach for Phase 2.
- `backtest/strategy_4h_hybrid.py` remains the reference implementation. Any changes to trading logic should be made there first, then mirrored to the decomposed modules.
