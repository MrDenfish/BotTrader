# Project Root Directory Analysis & Reorganization Plan

**Date:** December 15, 2025
**Current State:** 75+ files and directories in project root
**Goal:** Organize root directory for better maintainability and clarity

---

## Executive Summary

The project root contains **41 files** (excluding directories) with a mix of:
- ✅ **Essential files** (20) - Core functionality, deployment, configuration
- ⚠️ **Should be organized** (12) - Scripts, tests, utilities that belong in subdirectories
- ❌ **Obsolete/duplicates** (9) - Can be archived or deleted

---

## File Inventory by Category

### ✅ ESSENTIAL - Keep in Root (20 files)

#### Core Application Files
1. `main.py` (40K) - Main entry point ✅
2. `.env` (8.6K) - Environment configuration ✅
3. `.gitignore` (582B) - Git ignore rules ✅

#### Docker & Deployment
4. `docker-compose.aws.yml` (5.9K) - AWS production deployment ✅
5. `docker-compose.yml` (719B) - Local development ✅
6. `requirements.txt` (3.4K) - Python dependencies ✅
7. `requirements-report.txt` (1.0K) - Report service dependencies ✅
8. `requirements.in` (439B) - Pip-compile source ✅
9. `requirements-report.in` (144B) - Pip-compile source for reports ✅

#### Documentation (Current Session)
10. `SESSION_SUMMARY_DEC15_2025.md` (11K) - Current session ✅
11. `PASSIVE_MM_FIXES_SESSION.md` (12K) - Current session ✅
12. `DYNAMIC_FILTER_DOCUMENTATION.md` (17K) - Current session ✅
13. `DOCS_REORGANIZATION_SUMMARY.md` (7.8K) - Current session ✅
14. `ReadMe.md` (101B) - Project README (⚠️ needs expansion)

#### Configuration
15. `.env.dynamic_filter_example` (1.6K) - Config template ✅
16. `environment.yml` (531B) - Conda environment ✅

#### Hidden/System Files (Keep)
17. `.DS_Store` - macOS metadata (in .gitignore)
18. `.gitignore.bak` (510B) - Backup (can delete after verifying .gitignore works)
19. `.idea/` - PyCharm project settings
20. `.venv/` - Python virtual environment

---

### ⚠️ SHOULD BE ORGANIZED (12 files)

#### Test Files → Move to `tests/` directory
1. `test_config.py` (2.6K, Nov 7)
2. `test_fifo_engine.py` (15K, Nov 30)
3. `test_fifo_report.py` (5.2K, Nov 30)
4. `test_structured_logging.py` (5.3K, Nov 16)
5. `test_trailing_stop.py` (9.6K, Nov 30)

#### Diagnostic/Analysis Scripts → Move to `scripts/diagnostics/`
6. `analyze_logs.py` (15K, Nov 16)
7. `diagnostic_performance_analysis.py` (20K, Nov 16)
8. `diagnostic_signal_quality.py` (14K, Nov 16)
9. `verify_email_report.py` (9.8K, Dec 8)
10. `verify_report_accuracy.py` (6.8K, Dec 13)

#### Utility Scripts → Move to `scripts/utils/`
11. `extract_ground_truth.sh` (2.4K, Nov 1) - Data extraction
12. `investigate_sl_issue.py` (3.6K, Nov 30) - Investigation script

---

### ❌ OBSOLETE/DUPLICATES - Delete or Archive (9 files)

#### Obsolete Deployment Scripts
1. **`deploy_to_droplet.sh`** (878B, Nov 7) ❌ DELETE
   - **Why:** References DigitalOcean droplet (old hosting)
   - **Status:** Now using AWS (bottrader-aws)
   - **Action:** Delete (functionality replaced by AWS deployment)

2. **`bot_sync.sh`** (2.0K, May 3 2025) ❌ DELETE
   - **Why:** Laptop/desktop sync script (outdated logic)
   - **Status:** References "botdroplet" alias (DigitalOcean)
   - **Action:** Delete (AWS deployment uses different process)

3. **`sync_cleanup_and_push.sh`** (1.2K, May 25 2025) ❌ DELETE
   - **Why:** Part of old droplet deployment workflow
   - **Action:** Delete

4. **`sync_pull_and_deploy.sh`** (950B, May 3 2025) ❌ DELETE
   - **Why:** Part of old droplet deployment workflow
   - **Action:** Delete

5. **`run_report.sh`** (192B, Nov 16) ⚠️ REVIEW
   - **Status:** Replaced by Docker compose report-job
   - **Action:** Verify not used, then delete

6. **`run_report_once.sh`** (306B, Nov 16) ⚠️ REVIEW
   - **Status:** Likely superseded by Docker compose
   - **Action:** Verify not used, then delete

7. **`weekly_strategy_review.sh`** (2.7K, Dec 13) ⚠️ MOVE
   - **Status:** Active script for strategy optimization
   - **Action:** Move to `scripts/analytics/` or keep in root if run frequently

#### Obsolete Data Files
8. **`recent_orders.json`** (384K, Jul 1) ❌ ARCHIVE
   - **Why:** 5+ months old, likely stale
   - **Action:** Move to `data/archive/` or delete if not needed

9. **Email Reports** - 3 files ❌ MOVE
   - `Daily Trading Bot Report.eml` (28K, Dec 8)
   - `Daily Trading Bot Report_Dec12.eml` (34K, Dec 13)
   - `Daily Trading Bot Report_Dec13.eml` (36K, Dec 13)
   - **Why:** Sample email reports for testing/reference
   - **Action:** Move to `data/sample_reports/` or delete

#### Configuration Files
10. **`env_AWS`** (1.9K, Sep 1) ⚠️ VERIFY
    - **Why:** Possibly old AWS config (before .env consolidation)
    - **Action:** Compare with current `.env`, archive if duplicate

#### Empty/Broken Files
11. **`Discription`** (0B, Apr 23 2025) ❌ DELETE
    - **Why:** Empty file, typo in name
    - **Action:** Delete

12. **`discription.py`** (1.8K, Apr 23 2025) ⚠️ REVIEW
    - **Why:** Unclear purpose, old file
    - **Action:** Read contents, determine if needed

13. **`pre_fix_state.txt`** (4.6K, Nov 30) ⚠️ REVIEW
    - **Why:** Likely a snapshot before some fix
    - **Action:** Archive to `docs/archive/` if still relevant

14. **`project file tree`** (5.2K, Oct 28) ❌ DELETE
    - **Why:** Static file tree snapshot (outdated)
    - **Action:** Delete (can regenerate with `tree` command)

15. **`.gitignore.bak`** (510B) ❌ DELETE
    - **Why:** Backup of .gitignore (unnecessary if git history exists)
    - **Action:** Delete

---

## Proposed New Directory Structure

```
BotTrader/
├── README.md ← Expand from ReadMe.md (current 101B → 2-3KB)
├── main.py
├── .env
├── .gitignore
├── docker-compose.aws.yml
├── docker-compose.yml
├── requirements.txt
├── requirements-report.txt
├── requirements.in
├── requirements-report.in
├── environment.yml
├── .env.dynamic_filter_example
│
├── scripts/
│   ├── README.md
│   ├── deployment/
│   │   └── update.sh (AWS deployment script)
│   ├── diagnostics/
│   │   ├── analyze_logs.py
│   │   ├── diagnostic_performance_analysis.py
│   │   ├── diagnostic_signal_quality.py
│   │   ├── verify_email_report.py
│   │   └── verify_report_accuracy.py
│   ├── analytics/
│   │   └── weekly_strategy_review.sh
│   └── utils/
│       ├── extract_ground_truth.sh
│       └── investigate_sl_issue.py
│
├── tests/
│   ├── README.md
│   ├── test_config.py
│   ├── test_fifo_engine.py
│   ├── test_fifo_report.py
│   ├── test_structured_logging.py
│   └── test_trailing_stop.py
│
├── data/
│   ├── archive/
│   │   └── recent_orders_2025-07-01.json
│   └── sample_reports/
│       └── (email .eml files)
│
├── docs/ (already organized)
│
├── [Application directories...]
│   ├── AccumulationManager/
│   ├── Api_manager/
│   ├── Config/
│   ├── MarketDataManager/
│   ├── SharedDataManager/
│   ├── Shared_Utils/
│   ├── TableModels/
│   ├── botreport/
│   ├── database/
│   ├── database_manager/
│   ├── docker/
│   ├── sighook/ (likely in sighook/ directory)
│   └── webhook/ (likely in webhook/ directory)
│
└── [Current session docs - move to docs/archive/ when session ends]
    ├── SESSION_SUMMARY_DEC15_2025.md
    ├── PASSIVE_MM_FIXES_SESSION.md
    ├── DYNAMIC_FILTER_DOCUMENTATION.md
    └── DOCS_REORGANIZATION_SUMMARY.md
```

---

## Action Plan - Prioritized

### Priority 1: DELETE Obsolete Files (Safe, No Dependencies)

```bash
# Obsolete droplet deployment scripts
rm deploy_to_droplet.sh
rm bot_sync.sh
rm sync_cleanup_and_push.sh
rm sync_pull_and_deploy.sh

# Empty/broken files
rm Discription
rm .gitignore.bak

# Outdated snapshot
rm "project file tree"
```

### Priority 2: Create New Directories

```bash
mkdir -p scripts/{deployment,diagnostics,analytics,utils}
mkdir -p tests
mkdir -p data/{archive,sample_reports}
```

### Priority 3: Move Test Files

```bash
# Move test files
mv test_*.py tests/

# Create tests README
cat > tests/README.md << 'EOF'
# Test Suite

Unit and integration tests for BotTrader components.

## Running Tests

```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_fifo_engine.py

# Run with coverage
pytest --cov=. tests/
```

## Test Files

- `test_config.py` - Configuration validation tests
- `test_fifo_engine.py` - FIFO allocation engine tests
- `test_fifo_report.py` - FIFO reporting tests
- `test_structured_logging.py` - Logging system tests
- `test_trailing_stop.py` - Trailing stop logic tests
EOF
```

### Priority 4: Move Diagnostic Scripts

```bash
# Move diagnostic/analysis scripts
mv analyze_logs.py scripts/diagnostics/
mv diagnostic_performance_analysis.py scripts/diagnostics/
mv diagnostic_signal_quality.py scripts/diagnostics/
mv verify_email_report.py scripts/diagnostics/
mv verify_report_accuracy.py scripts/diagnostics/

# Move analytics
mv weekly_strategy_review.sh scripts/analytics/

# Move utilities
mv extract_ground_truth.sh scripts/utils/
mv investigate_sl_issue.py scripts/utils/
```

### Priority 5: Archive Data Files

```bash
# Archive old data
mv recent_orders.json data/archive/recent_orders_2025-07-01.json

# Archive sample email reports
mv *.eml data/sample_reports/ 2>/dev/null || true
```

### Priority 6: Review & Handle Special Cases

Files that need manual review:

1. **`discription.py`** - Read contents first
   ```bash
   cat discription.py
   # If unused: rm discription.py
   # If needed: mv discription.py scripts/utils/
   ```

2. **`env_AWS`** - Compare with current .env
   ```bash
   diff env_AWS .env
   # If duplicate: rm env_AWS
   # If has unique values: mv env_AWS data/archive/env_AWS_backup
   ```

3. **`pre_fix_state.txt`** - Determine relevance
   ```bash
   cat pre_fix_state.txt
   # If still relevant: mv pre_fix_state.txt docs/archive/
   # If obsolete: rm pre_fix_state.txt
   ```

4. **`run_report.sh` and `run_report_once.sh`** - Verify not in use
   ```bash
   grep -r "run_report" . --exclude-dir=.git
   # If not referenced anywhere: rm run_report*.sh
   ```

### Priority 7: Expand README.md

Current ReadMe.md is only 101 bytes with a typo. Replace with comprehensive README:

```bash
mv ReadMe.md ReadMe.md.old

cat > README.md << 'EOF'
# BotTrader

Cryptocurrency trading bot for Coinbase Advanced Trade with automated market making, FIFO allocation tracking, and dynamic symbol filtering.

## Features

- **Automated Trading:** Webhook-driven order execution via Coinbase websockets
- **Passive Market Making:** Spread-based market making with break-even exits
- **Dynamic Symbol Filtering:** Data-driven symbol exclusion based on performance
- **FIFO Accounting:** Accurate P&L tracking with FIFO allocation engine
- **Email Reports:** Automated daily performance reports via AWS SES
- **Position Monitoring:** Real-time position tracking with protective stop-losses

## Quick Start

### Local Development

```bash
# 1. Clone repository
git clone <repo-url>
cd BotTrader

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your API keys

# 4. Run locally
python main.py
```

### Production Deployment (AWS)

```bash
# Deploy to AWS server
ssh bottrader-aws "cd /opt/bot && ./update.sh"

# Check container status
ssh bottrader-aws "docker ps"

# View logs
ssh bottrader-aws "docker logs -f webhook"
```

## Architecture

- **webhook/** - Coinbase websocket listener & order execution
- **sighook/** - Trading signal generation & strategy management
- **botreport/** - Daily email reporting service
- **MarketDataManager/** - Market data & passive order management
- **Shared_Utils/** - Shared utilities including dynamic symbol filter
- **database/** - PostgreSQL schema & migrations

## Documentation

See `/docs/` directory for comprehensive documentation:

- [Architecture Deep Dive](docs/active/architecture/ARCHITECTURE_DEEP_DIVE.md)
- [AWS Deployment Checklist](docs/active/deployment/AWS_DEPLOYMENT_CHECKLIST.md)
- [Dynamic Symbol Filter](DYNAMIC_FILTER_DOCUMENTATION.md)

## Testing

```bash
# Run test suite
pytest tests/

# Run specific test
pytest tests/test_fifo_engine.py
```

## License

Private project - All rights reserved

---

**Last Updated:** December 15, 2025
EOF
```

---

## Summary Statistics

### Before Cleanup:
- **Total files in root:** 41
- **Python scripts:** 13
- **Shell scripts:** 8
- **Test files:** 5 (mixed with other scripts)
- **Obsolete files:** 9+
- **Documentation:** 4 current session docs

### After Cleanup:
- **Files remaining in root:** ~17 (essential only)
- **Python scripts in root:** 1 (main.py)
- **Organized into `scripts/`:** 12 files
- **Organized into `tests/`:** 5 files
- **Archived/deleted:** 9+ files
- **New directories:** `scripts/`, `tests/`, enhanced `data/`

### Benefits:
- ✅ **Clearer purpose** - Root only contains essential files
- ✅ **Better organization** - Tests, scripts, data properly categorized
- ✅ **Easier navigation** - Related files grouped together
- ✅ **Remove clutter** - Obsolete files deleted
- ✅ **Better README** - Proper project introduction (from 101B → 2KB+)

---

## Next Steps

1. **Review this analysis** with you for approval
2. **Execute Priority 1** (safe deletions)
3. **Execute Priorities 2-4** (create directories, move files)
4. **Manual review** Priority 6 files before action
5. **Expand README.md** with comprehensive content
6. **Test functionality** after reorganization
7. **Git commit** the reorganization

Would you like me to proceed with the reorganization?

---

**Created:** December 15, 2025
**Status:** Awaiting approval
