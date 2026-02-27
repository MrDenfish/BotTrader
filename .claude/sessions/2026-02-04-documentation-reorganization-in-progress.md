# Documentation Reorganization - In Progress

**Date**: 2026-02-04
**Branch**: `backtest/4h-hybrid-development` (current)
**Next Branch**: `refactor/plugin-architecture` (to be created)
**Status**: ✅ **COMPLETED** - Reorganization executed 2026-02-06
**Session**: Continuation after Claude Code upgrade to Opus 4.6

---

## Session Context

### What We Accomplished Today

1. ✅ **Closed all 2025 sessions** (8 sessions archived)
2. ✅ **Performed comprehensive code review** (111,536 tokens)
   - Found 4 CRITICAL issues (security, debug mode, silent exceptions)
   - Found 4 HIGH priority issues (monolithic file, race conditions)
   - Provided detailed fix recommendations
3. ✅ **Analyzed current docs directory structure**
4. ✅ **Created comprehensive reorganization plan**
5. ⏸️ **Awaiting user review** of proposed structure (Option C selected)

---

## User's Goal

> "I plan to refactor the BotTrader to support a plugin architecture for strategies and risk management, enabling the existing ROC momentum backtest strategy and the production signal-generation strategy to be tested independently or in tandem. Backtesting will be refactored to allow strategies to be plugged in for testing."

**Immediate Objectives**:
1. Clean up and organize docs directory
2. Create dedicated backtesting documentation section
3. Prepare structure for plugin architecture refactoring
4. Create new branch: `refactor/plugin-architecture`

---

## Current State

### Branch
- **Current**: `backtest/4h-hybrid-development`
- **Working directory**: Clean (all changes committed)
- **Last commit**: `ce85964` - "feat: Add 4h Hybrid Maker Strategy backtesting framework"

### Documentation Structure (Current)
```
docs/
├── BOTTRADER_OVERVIEW.md (NEW - Feb 4)
├── README.md
├── test-2-optimization.md (orphaned)
├── active/ (7 subdirs - production docs)
├── analysis/ (10 files - performance analysis)
├── archive/ (consolidated old docs)
├── in-progress/ (active work)
├── planning/ (20 files - mixed active/completed)
└── reminders/
```

**Issues**:
- No dedicated backtesting section
- Planning docs mixed (active Phase 2.x + completed TPSL)
- No space for plugin architecture design
- Orphaned files at root

---

## Proposed Structure (Awaiting Review)

### New Organization
```
docs/
├── 1-production/          # Live trading bot (from active/)
├── 2-backtesting/         # 🆕 Backtest framework & strategies
├── 3-plugin-architecture/ # 🆕 Plugin system design
├── 4-analysis/            # Performance analysis (from analysis/)
├── 5-planning/            # Active planning (cleaned up)
└── 6-archive/             # Historical docs (consolidated)
```

### Key Features

**2-backtesting/** (NEW):
```
2-backtesting/
├── strategies/
│   ├── 4h-hybrid-maker/      # All Phase 2.x docs
│   └── archived-strategies/  # Failed multi-ROC, etc.
├── guides/
│   ├── quickstart.md
│   ├── writing-strategies.md
│   └── interpreting-results.md
├── architecture/
│   └── state-machine.md
└── test-results/
```

**3-plugin-architecture/** (NEW):
```
3-plugin-architecture/
├── design/
│   ├── plugin-interface.md
│   ├── risk-management-plugin.md
│   └── data-feed-plugin.md
├── migration-plan/
│   ├── phase-1-extract-interfaces.md
│   ├── phase-2-refactor-strategies.md
│   └── phase-3-testing-framework.md
└── examples/
```

---

## Questions Pending User Response

### Structure Questions

1. **Numbering scheme**: Happy with `1-production/`, `2-backtesting/`, etc.?

2. **Backtest strategies organization**: Organize by:
   - Strategy type (momentum, mean-reversion)?
   - Timeframe (1m, 5m, 4h, 1d)?
   - Current structure (by name)?

3. **Plugin architecture sections**: Need separate docs for each plugin type?

4. **Analysis section**: Split by type/date or keep flat?

### Content Questions

5. **Extract from `.claude/sessions/`**: Which session notes should become formal docs?

6. **Planning docs disposition**:
   - Phase 2.x → `2-backtesting/strategies/4h-hybrid-maker/`?
   - TPSL → `5-planning/completed/` or `6-archive/`?
   - ROC spec → Archive?

7. **README granularity**: READMEs for each strategy or just top-level dirs?

---

## Files Created This Session

1. **`.claude/sessions/2026-02-04-project-overview-and-branch-setup.md`**
   - Complete session summary
   - Documents all work (overview, fee analysis, branch creation)
   - ✅ Marked complete

2. **`docs/BOTTRADER_OVERVIEW.md`**
   - Comprehensive project overview
   - Production architecture
   - Backtest strategy details
   - Fee structure analysis
   - Production vs backtest comparison (CRITICAL MISMATCH found)

3. **`docs/REORGANIZATION_PLAN_2026-02-04.md`**
   - Detailed reorganization plan
   - Step-by-step migration actions
   - Time estimates (3.5 hours total)
   - Verification checklist

4. **`.claude/sessions/ARCHIVE_2025_CLOSURE.md`**
   - Summary of 8 closed 2025 sessions
   - Index of all archived work

5. **`.claude/sessions/2026-02-04-documentation-reorganization-in-progress.md`**
   - This file (session state)

---

## Code Review Findings (Summary)

### 🔴 CRITICAL (Must Fix Before Production)

1. **API Key Metadata Logging** (`main.py:814-820`)
   - Logs secret length → aids brute force
   - Fix: Remove `sec_len` and `pp_len` from logs

2. **Debug Mode Enabled** (`main.py:1015`)
   - `PYTHONASYNCIODEBUG = '1'` in production
   - Fix: Disable for production builds

3. **Silent WebSocket Exceptions** (`listener.py:273-275`)
   - `except Exception: pass` during reconnection
   - Fix: Log all exceptions with alerting

4. **Disabled Strategy Code** (`signal_manager.py:367-403`)
   - `if False:` blocks without feature flags
   - Fix: Proper environment variable flags

### 🟡 HIGH Priority

5. **Monolithic Strategy File** (`strategy_4h_hybrid.py` - 2,813 lines)
   - Violates Single Responsibility
   - Recommendation: Split into 5 modules

6. **No Circuit Breaker** (`sender.py:365-377`)
   - API failures crash bot
   - Fix: Exponential backoff pattern

7. **Race Conditions** (`listener.py:716-718`)
   - Order tracker mutations not atomic
   - Fix: Wrap with locks

### Security

8. **Upgrade `cryptography` to 45.0.7+** (CVE fix)

**Estimated Time to Production-Ready**: 2 days (16 hours)

---

## Next Steps (When Resuming)

### Immediate (This Session)
1. **Review user feedback** on proposed docs structure
2. **Make modifications** based on feedback
3. **Execute reorganization** (automated or manual)
4. **Create new branch**: `refactor/plugin-architecture`
5. **Commit documentation changes**

### Short-Term (Next Session)
1. Work with ChatGPT on detailed plugin refactoring plan
2. Extract strategy interfaces from existing code
3. Begin splitting monolithic strategy file

### Medium-Term (Sprint Work)
1. Implement plugin architecture
2. Refactor production strategy as plugin
3. Refactor 4h hybrid backtest as plugin
4. Create testing framework for plugins

---

## Commands to Resume Work

### Check Current State
```bash
cd /Users/Manny/Python_Projects/BotTrader
git status
git branch --show-current
```

### Review Proposed Structure
```bash
cat docs/REORGANIZATION_PLAN_2026-02-04.md
```

### When Ready to Execute
```bash
# Option 1: Automated reorganization (I can script this)
# Option 2: Manual step-by-step (you approve each move)
# Option 3: Hybrid (create structure, you move files)
```

---

## Key Files for Reference

### Documentation
- `docs/BOTTRADER_OVERVIEW.md` - Project overview
- `docs/REORGANIZATION_PLAN_2026-02-04.md` - Detailed plan
- `.claude/sessions/2026-02-04-project-overview-and-branch-setup.md` - Today's work

### Code Review
- Full review in agent output (111,536 tokens)
- Critical findings documented above
- Prioritized fix list ready

### Session Notes
- All 2025 sessions closed ✅
- 7 open 2026 sessions remain (to review later)
- Current session state saved here

---

## User's Last Request

> "I will need to close the terminal while updating claude code to Claude Opus 4.6. I would like to pick up here when I return. Can you save the progress made?"

**Response**: ✅ Progress saved in this file

---

## Resume Point

**When you return**:
1. Say "Continue documentation reorganization"
2. I'll present the proposed structure for your review
3. You provide feedback/modifications
4. We execute the reorganization
5. Create the new branch
6. Begin plugin architecture planning with ChatGPT

**Current Question**: Review the proposed structure in `docs/REORGANIZATION_PLAN_2026-02-04.md` and provide feedback on:
- Numbering scheme (1-production, 2-backtesting, etc.)
- Directory organization
- Subsection structure
- Content placement

---

**Status**: ✅ **COMPLETED**
**Last Updated**: 2026-02-06
**Completed**: Documentation reorganization executed successfully
