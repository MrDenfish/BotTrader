# Kraken Exchange Integration - Adding Kraken as second exchange to v2

## Session Overview
- **Started:** 2026-02-17 12:15
- **Branch:** `feature/kraken-exchange` (to be created)
- **Status:** On hold — plan approved, awaiting API key setup

## Goals
1. Add Kraken as a second exchange option in v2 alongside Coinbase
2. New plugins: KrakenExchange adapter, Kraken WebSocket data provider, Kraken pair discovery
3. Symbol mapper utility for bidirectional translation (BTC-USD ↔ XBTUSD/XBT/USD)
4. Kraken paper trading config file
5. Run parallel paper trading (Kraken vs Coinbase) to validate

## Context
- Coinbase fee schedule penalizes low-volume months (0.43% at <$15K vs Kraken's 0.25%)
- v2 plugin architecture makes this clean — exchange-agnostic strategy/risk/execution layers
- Plan file: `.claude/plans/dreamy-sparking-bee.md`
- Prerequisite: User needs to create Kraken API keys

## Pre-requisites
- [ ] Create Kraken Pro API keys (Query funds, Query/Create/Cancel orders)
- [ ] Store as `KRAKEN_API_KEY` and `KRAKEN_API_SECRET` env vars

## Progress
- [x] Fee comparison research (Kraken wins at every tier, especially low volume)
- [x] Codebase audit of Coinbase touchpoints (3 plugins, all isolated)
- [x] Kraken API research (REST, WebSocket v2, python-kraken-sdk)
- [x] Implementation plan written and approved
- [ ] Create feature branch
- [ ] Phase 1: Symbol mapper utility
- [ ] Phase 2: Kraken pair discovery plugin
- [ ] Phase 3: Kraken WebSocket data provider
- [ ] Phase 4: Kraken exchange adapter
- [ ] Phase 5: Config + requirements
- [ ] Phase 6: Tests
- [ ] Phase 7: Paper trading validation
