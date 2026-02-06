# Documentation Reorganization Plan

**Date**: 2026-02-04
**Branch**: To be created - `refactor/plugin-architecture`
**Objective**: Organize docs for plugin architecture refactoring and backtest strategy development

---

## Current State Analysis

### Existing Directory Structure
```
docs/
├── BOTTRADER_OVERVIEW.md (NEW - keep at root)
├── README.md (keep at root)
├── test-2-optimization.md (orphaned file)
├── active/               # Current production docs
│   ├── architecture/
│   ├── deployment/
│   ├── features/
│   ├── guides/
│   └── operations/
├── analysis/             # Performance and system analysis
├── archive/              # Old documentation
│   ├── analysis/
│   ├── bugs-resolved/
│   ├── deprecated/
│   ├── planning/
│   ├── roc-multi-strategy/
│   └── sessions/
├── in-progress/          # Active work docs
├── planning/             # Mix of active and completed plans (20 files)
└── reminders/
```

### Issues Identified
1. **Mixed State**: `planning/` contains both active (Phase 2.x backtest) and obsolete (TPSL) docs
2. **No Backtest Section**: Backtest strategy docs scattered across `planning/` and `archive/`
3. **Orphaned Files**: `test-2-optimization.md` at root
4. **Duplicate Content**: Some archive folders duplicate active content

---

## Proposed New Structure

```
docs/
├── README.md                          # Overview of documentation structure
├── BOTTRADER_OVERVIEW.md              # High-level project overview (NEW)
│
├── 1-production/                      # Live trading bot documentation
│   ├── README.md
│   ├── architecture/
│   │   ├── overview.md
│   │   ├── dual-container-design.md
│   │   ├── fifo-accounting.md
│   │   ├── websocket-handling.md
│   │   └── cross-container-sync.md
│   ├── strategies/
│   │   ├── composite-scoring.md      # Current production strategy
│   │   ├── signal-generation.md
│   │   └── exit-logic.md
│   ├── deployment/
│   │   ├── aws-deployment.md
│   │   ├── docker-compose.md
│   │   └── monitoring.md
│   ├── operations/
│   │   ├── daily-operations.md
│   │   ├── troubleshooting.md
│   │   └── performance-tuning.md
│   └── api-reference/
│       └── (existing API docs)
│
├── 2-backtesting/                     # Backtesting framework (NEW)
│   ├── README.md                      # Backtest overview and quickstart
│   ├── architecture/
│   │   ├── overview.md
│   │   ├── data-pipeline.md
│   │   ├── event-driven-engine.md
│   │   └── state-machine.md
│   ├── strategies/
│   │   ├── 4h-hybrid-maker/
│   │   │   ├── README.md
│   │   │   ├── phase2-1-chase-hardening.md
│   │   │   ├── phase2-2-state-refactor.md
│   │   │   ├── phase2-3-optimization.md
│   │   │   ├── phase2-4-stage-c-runner.md
│   │   │   └── configuration.md
│   │   ├── archived-strategies/
│   │   │   ├── multi-roc-1m.md
│   │   │   └── multi-tf-experimental.md
│   │   └── strategy-template.md
│   ├── guides/
│   │   ├── quickstart.md
│   │   ├── writing-strategies.md
│   │   ├── data-preparation.md
│   │   ├── parameter-optimization.md
│   │   └── interpreting-results.md
│   ├── analysis/
│   │   ├── fee-structure-analysis.md
│   │   ├── timeframe-comparison.md
│   │   └── production-vs-backtest.md
│   └── test-results/
│       ├── phase2-1-results.md
│       ├── phase2-3-results.md
│       └── stage-c-ab-test.md
│
├── 3-plugin-architecture/             # NEW - Plugin system design
│   ├── README.md                      # Architecture overview
│   ├── design/
│   │   ├── plugin-interface.md        # Strategy plugin interface
│   │   ├── risk-management-plugin.md  # Risk management interface
│   │   ├── data-feed-plugin.md        # Data source interface
│   │   └── execution-plugin.md        # Order execution interface
│   ├── migration-plan/
│   │   ├── phase-1-extract-interfaces.md
│   │   ├── phase-2-refactor-strategies.md
│   │   ├── phase-3-testing-framework.md
│   │   └── phase-4-production-deploy.md
│   └── examples/
│       ├── simple-ma-crossover.md     # Example strategy plugin
│       └── fixed-risk-manager.md      # Example risk plugin
│
├── 4-analysis/                        # System analysis and performance
│   ├── README.md
│   ├── performance/
│   │   ├── 2025-12-performance-analysis.md
│   │   ├── strategy-performance-tracking.md
│   │   └── database-maintenance-analysis.md
│   ├── feasibility/
│   │   └── multi-exchange-assessment.md
│   └── issues/
│       └── risk-capital-metrics-issue.md
│
├── 5-planning/                        # Active planning documents
│   ├── README.md
│   ├── active/                        # Current sprint/active work
│   │   └── plugin-architecture-refactor.md (NEW)
│   └── completed/                     # Move completed plans here
│       ├── tpsl-coordination.md
│       ├── schema-cleanup.md
│       └── (other completed plans)
│
└── 6-archive/                         # Historical documentation
    ├── README.md                      # Archive index with dates
    ├── strategies/                    # Old strategy attempts
    │   └── roc-multi-strategy/
    ├── bugs-resolved/                 # Keep existing
    ├── sessions/                      # Keep existing
    ├── analysis/                      # Old analysis docs
    ├── planning/                      # Superseded plans
    └── deprecated/                    # Keep existing
```

---

## Migration Actions

### Phase 1: Create New Structure (Est: 30 min)

**Create Directories**:
```bash
mkdir -p docs/1-production/{architecture,strategies,deployment,operations,api-reference}
mkdir -p docs/2-backtesting/{architecture,strategies/4h-hybrid-maker,strategies/archived-strategies,guides,analysis,test-results}
mkdir -p docs/3-plugin-architecture/{design,migration-plan,examples}
mkdir -p docs/4-analysis/{performance,feasibility,issues}
mkdir -p docs/5-planning/{active,completed}
mkdir -p docs/6-archive/{strategies,bugs-resolved,sessions,analysis,planning,deprecated}
```

### Phase 2: Move Production Docs (Est: 20 min)

**From `active/` to `1-production/`**:
```bash
# Architecture docs
mv docs/active/architecture/* docs/1-production/architecture/

# Deployment docs
mv docs/active/deployment/* docs/1-production/deployment/

# Operations guides
mv docs/active/operations/* docs/1-production/operations/

# Features → strategies
mv docs/active/features/* docs/1-production/strategies/

# Guides
mv docs/active/guides/* docs/1-production/operations/
```

### Phase 3: Create Backtesting Section (Est: 45 min)

**Move from `planning/` to `2-backtesting/strategies/4h-hybrid-maker/`**:
```bash
mv docs/planning/phase2_2_implementation_progress.md docs/2-backtesting/strategies/4h-hybrid-maker/
mv docs/planning/phase2_2_patch_spec_4h_hybrid_fillrate.md docs/2-backtesting/strategies/4h-hybrid-maker/
mv docs/planning/phase2_2_state_machine_refactor_guide.md docs/2-backtesting/strategies/4h-hybrid-maker/
mv docs/planning/phase2_3_optimization_plan*.md docs/2-backtesting/strategies/4h-hybrid-maker/
mv docs/planning/phase2_3_task_breakdown.md docs/2-backtesting/strategies/4h-hybrid-maker/
mv docs/planning/phase2_implementation_plan*.md docs/2-backtesting/strategies/4h-hybrid-maker/
mv docs/planning/phase2_spec_4h_hybrid_compression_chase.md docs/2-backtesting/strategies/4h-hybrid-maker/
mv docs/planning/strategy_hybrid_4h_fee_aware_post_only.md docs/2-backtesting/strategies/4h-hybrid-maker/
```

**Move archived strategies**:
```bash
mv docs/archive/roc-multi-strategy/* docs/2-backtesting/strategies/archived-strategies/
```

**Create new guide files** (from session notes):
- Extract quickstart from `.claude/sessions/2026-01-30-4h-hybrid-maker-strategy.md`
- Create `writing-strategies.md` template
- Document fee structure analysis (from today's session)

### Phase 4: Move Analysis Docs (Est: 15 min)

**From `analysis/` to `4-analysis/performance/`**:
```bash
mv docs/analysis/PERFORMANCE_ANALYSIS_2025-12-03.md docs/4-analysis/performance/
mv docs/analysis/STRATEGY_PERFORMANCE_TRACKING.md docs/4-analysis/performance/
mv docs/analysis/DATABASE_MAINTENANCE_ANALYSIS.md docs/4-analysis/performance/
mv docs/analysis/email_report_verification_results.md docs/4-analysis/performance/
```

**Move feasibility studies**:
```bash
mv docs/analysis/multi-exchange-feasibility-assessment.md docs/4-analysis/feasibility/
```

**Move issue docs**:
```bash
mv docs/analysis/RISK_CAPITAL_METRICS_ISSUE.md docs/4-analysis/issues/
```

### Phase 5: Organize Planning Docs (Est: 20 min)

**Completed plans to `5-planning/completed/`**:
```bash
# TPSL plans (completed)
mv docs/planning/TPSL*.md docs/5-planning/completed/

# Schema cleanup (completed)
mv docs/planning/*SCHEMA*.md docs/5-planning/completed/

# ROC strategy (superseded by 4h hybrid)
mv docs/planning/roc_dual_atrpct_strategy_spec.md docs/6-archive/strategies/
```

**Keep in `5-planning/active/`** (no move, actively referenced):
- Any ongoing planning docs (none currently)

### Phase 6: Consolidate Archive (Est: 15 min)

**Archive old planning docs**:
```bash
# Move old archive/planning to 6-archive/planning
mv docs/archive/planning/* docs/6-archive/planning/

# Preserve bugs-resolved
mv docs/archive/bugs-resolved/* docs/6-archive/bugs-resolved/

# Preserve sessions
mv docs/archive/sessions/* docs/6-archive/sessions/

# Move old analysis
mv docs/archive/analysis/* docs/6-archive/analysis/
```

### Phase 7: Create Plugin Architecture Docs (Est: 30 min)

**New files to create**:
```bash
# docs/3-plugin-architecture/README.md
# docs/3-plugin-architecture/design/plugin-interface.md
# docs/3-plugin-architecture/migration-plan/overview.md
```

Content based on:
- Code review findings (monolithic strategy file)
- Production vs backtest strategy mismatch
- Need for independent strategy testing

### Phase 8: Create Index READMEs (Est: 20 min)

**Create README.md for each top-level directory**:
- `1-production/README.md` - Production docs overview
- `2-backtesting/README.md` - Backtest quickstart and index
- `3-plugin-architecture/README.md` - Plugin system overview
- `4-analysis/README.md` - Analysis document index
- `5-planning/README.md` - Planning process and active items
- `6-archive/README.md` - Archive index with dates

### Phase 9: Clean Up (Est: 10 min)

**Remove empty directories**:
```bash
rmdir docs/active docs/in-progress docs/reminders docs/analysis docs/planning
```

**Handle orphaned files**:
```bash
# Move test-2-optimization.md to archive
mv docs/test-2-optimization.md docs/6-archive/planning/
```

---

## New Branch Creation

### Branch Strategy

**Branch Name**: `refactor/plugin-architecture`

**Purpose**: Comprehensive refactoring to support plugin-based strategy architecture

**Scope**:
1. Documentation reorganization (this plan)
2. Extract strategy interfaces
3. Refactor 2,813-line strategy file
4. Enable independent strategy testing
5. Support production signal-generator as plugin
6. Support 4h hybrid backtest as plugin

### Create Branch

```bash
# Ensure we're on latest backtest branch
git checkout backtest/4h-hybrid-development
git pull origin backtest/4h-hybrid-development

# Create new feature branch
git checkout -b refactor/plugin-architecture

# Verify branch
git branch --show-current
```

---

## Documentation Style Guide

### File Naming Conventions
- Use kebab-case: `plugin-interface.md`
- Prefix with numbers for ordering: `phase-1-extract.md`
- Date format in filenames: `2026-02-04-overview.md`

### README Structure
```markdown
# [Directory Name]

## Overview
Brief description of directory contents

## Contents
- **subdirectory/**: Description
- `filename.md`: Description

## Quick Links
- [Most important doc]
- [Second most important]

## Last Updated
YYYY-MM-DD
```

### Document Headers
```markdown
# Document Title

**Date**: YYYY-MM-DD
**Status**: Draft | Active | Completed | Archived
**Related**: Links to related docs

## Overview
Brief summary

## [Main Sections]
...
```

---

## Verification Checklist

After reorganization:
- [ ] All files have been moved to new locations
- [ ] No broken internal links (update cross-references)
- [ ] README.md exists in each top-level directory
- [ ] Root `docs/README.md` updated with new structure
- [ ] `BOTTRADER_OVERVIEW.md` remains at root
- [ ] Archive index created with dates
- [ ] Orphaned files resolved
- [ ] Empty directories removed
- [ ] Git status clean (all moves tracked)

---

## Timeline

**Total Estimated Time**: 3.5 hours

| Phase | Task | Time |
|-------|------|------|
| 1 | Create directories | 30 min |
| 2 | Move production docs | 20 min |
| 3 | Create backtesting section | 45 min |
| 4 | Move analysis docs | 15 min |
| 5 | Organize planning docs | 20 min |
| 6 | Consolidate archive | 15 min |
| 7 | Create plugin docs | 30 min |
| 8 | Create READMEs | 20 min |
| 9 | Clean up | 10 min |
| **Total** | | **3h 25min** |

---

## Next Steps After Reorganization

1. **Commit documentation changes**
   ```bash
   git add docs/
   git commit -m "docs: Reorganize documentation for plugin architecture

   - Create production, backtesting, and plugin architecture sections
   - Consolidate backtest strategy docs under 2-backtesting/
   - Archive obsolete planning and analysis docs
   - Prepare structure for plugin system refactoring

   Co-Authored-By: Claude <noreply@anthropic.com>"
   ```

2. **Push branch to GitHub**
   ```bash
   git push -u origin refactor/plugin-architecture
   ```

3. **Work with ChatGPT on detailed refactoring plan**
   - Share new docs structure
   - Design plugin interfaces
   - Plan migration phases

4. **Begin code refactoring**
   - Extract strategy interfaces
   - Split monolithic strategy file
   - Implement plugin system

---

**Created**: 2026-02-04
**Author**: Claude Code
**Status**: Ready for execution
