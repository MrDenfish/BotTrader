# Backtesting

Backtesting framework documentation, strategy specifications, and test results.

## Contents

- **strategies/** - Strategy implementations and specifications
  - **4h-hybrid-maker/** - 4-hour hybrid maker strategy (Phase 2.x development)
  - **archived-strategies/** - Superseded strategies (ROC multi-strategy, etc.)
- **architecture/** - Backtest engine architecture (to be documented)
- **guides/** - How-to guides for writing and running backtests (to be created)
- **analysis/** - Backtest-specific analysis (fee structures, comparisons)
- **test-results/** - Backtest run outputs and comparisons

## 4h Hybrid Maker Strategy

The primary backtest strategy under development. Documentation covers:

- Phase 2.1: Chase hardening and fill rate improvements
- Phase 2.2: State machine refactor
- Phase 2.3: Parameter optimization (v1-v4)
- Fee-aware post-only execution model
- Compression and chase logic specifications

See [strategies/4h-hybrid-maker/](strategies/4h-hybrid-maker/) for all docs.

## Archived Strategies

- ROC multi-strategy system (superseded by 4h hybrid)
- ROC dual ATR-PCT strategy spec

## Last Updated

2026-02-06
