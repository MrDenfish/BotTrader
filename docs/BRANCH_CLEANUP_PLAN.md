# Branch Cleanup Plan - Using Naming Convention

**Date:** 2025-12-02
**Strategy:** Option 1 - Branch Naming Convention

---

## Summary

- **8 branches** fully merged to main → **DELETE (archive)**
- **7 branches** not yet merged → **REVIEW (keep or rename)**

---

## ✅ SAFE TO DELETE - Fully Merged Branches (8 total)

These branches are 100% merged into `main`. All commits exist in main branch history.

### Cleanup Action:
```bash
# Delete locally and remotely
git branch -d <branch-name>
git push origin --delete <branch-name>
```

### List:

#### 1. `feature/smart-limit-exits` ✅
- **Last commit:** 2025-12-02
- **Message:** fix: Prevent decimal.InvalidOperation and NoneType comparison errors
- **Status:** JUST MERGED TODAY (commit 372b5cc)
- **Action:** DELETE (work is complete and in main)

#### 2. `feature/signal-based-exits` ✅
- **Last commit:** 2025-11-30
- **Message:** fix: Preprocess DB data before merging in save_data
- **Status:** Merged in commit 736c393
- **Action:** DELETE (Phase 5 complete and in main)

#### 3. `feature/fifo-allocations-redesign` ✅
- **Last commit:** 2025-11-23
- **Message:** feat: Implement smart LIMIT-only exit strategy with P&L thresholds
- **Status:** Merged
- **Action:** DELETE (FIFO work merged)

#### 4. `phase2-session1-organization` ✅
- **Last commit:** 2025-11-02
- **Message:** Add code organization: Table of Contents and section markers
- **Status:** Old refactoring work, merged
- **Action:** DELETE (ancient branch, fully merged)

#### 5. `phase2-session2-constants` ✅
- **Last commit:** 2025-11-03
- **Message:** Complete multi-environment Config system integration
- **Status:** Old refactoring work, merged
- **Action:** DELETE (ancient branch, fully merged)

#### 6. `claude/structured-logging-fixes-011CV3MNtZRyXEwJN3pBkPVu` ✅
- **Last commit:** 2025-11-12
- **Message:** fix: Update diagnostic_signal_quality.py to look for scores.jsonl
- **Status:** Auto-generated Claude branch, merged
- **Action:** DELETE (temporary work branch)

#### 7. `claude/structured-logging-foundation-011CUv7LVh354k4hoB15Epoa` ✅
- **Last commit:** 2025-11-11
- **Message:** fix: Handle None db connection in close() method
- **Status:** Auto-generated Claude branch, merged
- **Action:** DELETE (temporary work branch)

#### 8. `claude/work-in-progress-011CUuCNZCm3Vc8HHyKrV3BR` ✅
- **Last commit:** 2025-11-07
- **Message:** refactor: Unify environment configuration to single .env file
- **Status:** Auto-generated Claude branch, merged
- **Action:** DELETE (temporary work branch)

---

## 🔄 REVIEW - Unmerged Branches (7 total)

These branches have commits NOT in main. Need review before deleting.

### Option A: Keep Active Development
### Option B: Archive if abandoned
### Option C: Merge if ready

---

### ACTIVE DEVELOPMENT (Keep & Possibly Rename)

#### 1. `feature/tpsl-optimization` 🔄
- **Last commit:** 2025-12-02
- **Commits ahead:** 5
- **Message:** fix: Prevent infinite loop in BUY/SELL parent_ids maintenance
- **Status:** ACTIVE - Contains exit_reason tracking, production testing
- **Action:** **KEEP** - Rename to `active/tpsl-optimization` (if using naming convention)
- **Notes:** Has ARCHITECTURE_DEEP_DIVE.md and exit_reason field

#### 2. `feature/profitability-optimization` 🔄
- **Last commit:** 2025-11-30
- **Commits ahead:** 2
- **Message:** fix: CRITICAL - Correct blacklist using FIFO allocations data
- **Status:** ACTIVE - Symbol blacklist feature
- **Action:** **KEEP** - Rename to `active/profitability-optimization`
- **Notes:** Experimental blacklist, needs FIFO validation

---

### REVIEW NEEDED (Check if Still Relevant)

#### 3. `feature/hybrid-order-management` ⚠️
- **Last commit:** 2025-11-19
- **Commits ahead:** 23
- **Message:** fix: Enable monitoring for webhook limit-only positions
- **Status:** OLD (13 days) - May be superseded
- **Action:** **REVIEW** - Check if superseded by smart-limit-exits
- **Notes:** 23 commits diverged from main

#### 4. `fix/pnl-calculation-bug` ⚠️
- **Last commit:** 2025-11-20
- **Commits ahead:** 25
- **Message:** fix: Add protection for SELL records during reconciliation
- **Status:** OLD (12 days) - May be superseded
- **Action:** **REVIEW** - Check if bug already fixed in main
- **Notes:** 25 commits diverged from main

#### 5. `feature/structured-logging` ⚠️
- **Last commit:** 2025-11-11
- **Commits ahead:** 6
- **Message:** fix(report): Update email report with new delivery timestamps
- **Status:** OLD (21 days) - Likely merged via other branches
- **Action:** **REVIEW** - Check if logging work is in main
- **Notes:** May be duplicate of claude/structured-logging branches

#### 6. `backup-feature-structured-logging` ⚠️
- **Last commit:** 2025-11-11
- **Commits ahead:** 6
- **Message:** fix(report): Update email report with new delivery timestamps
- **Status:** BACKUP - Same as feature/structured-logging
- **Action:** **DELETE** - Backup copy, can recreate from main
- **Notes:** Identical to feature/structured-logging

#### 7. `claude/parameter-tuning-reports-011CV4hhiR6CNdTgBUPLGM5u` ⚠️
- **Last commit:** 2025-11-16
- **Commits ahead:** 18
- **Message:** Merge branch 'main' into claude/parameter-tuning-reports-*
- **Status:** Auto-generated Claude branch
- **Action:** **REVIEW** - Check if parameter tuning work is needed
- **Notes:** 18 commits ahead, may have useful experiments

---

## Proposed Cleanup Commands

### Phase 1: Delete Merged Branches (Safe - Run Now)

```bash
# Delete locally
git branch -d feature/smart-limit-exits
git branch -d feature/signal-based-exits
git branch -d feature/fifo-allocations-redesign
git branch -d phase2-session1-organization
git branch -d phase2-session2-constants
git branch -d claude/structured-logging-fixes-011CV3MNtZRyXEwJN3pBkPVu
git branch -d claude/structured-logging-foundation-011CUv7LVh354k4hoB15Epoa
git branch -d claude/work-in-progress-011CUuCNZCm3Vc8HHyKrV3BR

# Delete remotely (GitHub)
git push origin --delete feature/smart-limit-exits
git push origin --delete feature/signal-based-exits
git push origin --delete feature/fifo-allocations-redesign
git push origin --delete phase2-session1-organization
git push origin --delete phase2-session2-constants
git push origin --delete claude/structured-logging-fixes-011CV3MNtZRyXEwJN3pBkPVu
git push origin --delete claude/structured-logging-foundation-011CUv7LVh354k4hoB15Epoa
git push origin --delete claude/work-in-progress-011CUuCNZCm3Vc8HHyKrV3BR
```

**Result:** 8 branches deleted (local + remote)

---

### Phase 2: Rename Active Branches (Optional - Use Naming Convention)

```bash
# Rename active development branches
git branch -m feature/tpsl-optimization active/tpsl-optimization
git push origin active/tpsl-optimization
git push origin --delete feature/tpsl-optimization

git branch -m feature/profitability-optimization active/profitability-optimization
git push origin active/profitability-optimization
git push origin --delete feature/profitability-optimization
```

**Result:** Active branches clearly marked with `active/` prefix

---

### Phase 3: Review Old Branches (Manual Decision Required)

For each old branch, check what's unique:

```bash
# See what's different from main
git log main..feature/hybrid-order-management --oneline

# See what files changed
git diff main...feature/hybrid-order-management --stat

# If nothing useful, delete it
git branch -D feature/hybrid-order-management  # Force delete if not merged
git push origin --delete feature/hybrid-order-management
```

**Candidates for deletion after review:**
- `feature/hybrid-order-management` (likely superseded)
- `fix/pnl-calculation-bug` (likely fixed in main)
- `feature/structured-logging` (likely merged via other branches)
- `backup-feature-structured-logging` (backup copy)
- `claude/parameter-tuning-reports-*` (temp work branch)

---

## Branch Naming Convention (Going Forward)

Use these prefixes for new branches:

```
active/feature-name       # Active development
archive/feature-name      # Completed work (before deleting)
experiment/feature-name   # Experimental/research
fix/bug-name              # Bug fixes
cleanup/task-name         # Refactoring/cleanup work
```

### Benefits:
- `git branch` output self-organizes alphabetically
- Easy filtering: `git branch | grep "active/"`
- Clear status at a glance
- No need for external tracking tools

---

## Summary

### Immediate Actions (Safe):
✅ Delete 8 fully merged branches (local + remote)

### Follow-up Actions (After Review):
🔍 Review 5 old/abandoned branches
✨ Rename 2 active branches (optional)

### Final State:
- `main` - production code
- `active/tpsl-optimization` - exit_reason testing
- `active/profitability-optimization` - blacklist feature

Clean, organized, easy to understand! 🎉
