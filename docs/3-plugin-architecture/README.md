# Plugin Architecture

Design and migration plan for refactoring BotTrader to support a plugin-based architecture for strategies and risk management.

## Goal

Enable the existing ROC momentum backtest strategy and the production signal-generation strategy to be tested independently or in tandem. Backtesting will be refactored to allow strategies to be plugged in for testing.

## Contents

- **design/** - Plugin interface specifications
  - `plugin-interface.md` - Strategy plugin contract (TODO)
  - `risk-management-plugin.md` - Risk management interface (TODO)
  - `data-feed-plugin.md` - Data source abstraction (TODO)
  - `execution-plugin.md` - Order execution interface (TODO)
- **migration-plan/** - Phased migration from monolithic to plugin architecture
  - Phase 1: Extract interfaces from existing code
  - Phase 2: Refactor strategies as plugins
  - Phase 3: Build testing framework
  - Phase 4: Production deployment
- **examples/** - Example plugin implementations (TODO)

## Key Motivations

1. **Monolithic strategy file** (`strategy_4h_hybrid.py` - 2,813 lines) needs decomposition
2. **Independent testing** - Run production and backtest strategies through same framework
3. **Strategy comparison** - A/B test strategies with consistent infrastructure
4. **Extensibility** - New strategies without modifying core engine

## Status

Placeholder - detailed design to be developed.

## Last Updated

2026-02-06
