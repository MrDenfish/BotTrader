# Scripts Directory

Utility scripts, diagnostics, and automation tools for BotTrader.

## Directory Structure

### diagnostics/
Tools for analyzing system behavior and verifying correctness:
- `analyze_logs.py` - Parse and analyze bot logs
- `diagnostic_performance_analysis.py` - Performance metrics analysis
- `diagnostic_signal_quality.py` - Trading signal quality analysis
- `verify_email_report.py` - Verify email report accuracy
- `verify_report_accuracy.py` - Verify P&L calculations

### analytics/
Data analysis and strategy evaluation:
- `weekly_strategy_review.sh` - Generate weekly performance reports (runs on cron)

### utils/
General utility scripts:
- `extract_ground_truth.sh` - Extract ground truth data from exchange
- `investigate_sl_issue.py` - Investigate stop-loss issues

### deployment/
Scripts already exist in this directory for AWS deployment.

### migrations/
Database migration scripts (already exist).

## Usage

### Run Diagnostics

```bash
# Analyze recent logs
python scripts/diagnostics/analyze_logs.py

# Check email report accuracy
python scripts/diagnostics/verify_email_report.py

# Performance analysis
python scripts/diagnostics/diagnostic_performance_analysis.py
```

### Weekly Analytics

```bash
# Manual run
bash scripts/analytics/weekly_strategy_review.sh

# Output saved to /opt/bot/logs/weekly_review_YYYY-MM-DD.txt on server
```

### Utilities

```bash
# Extract ground truth data
bash scripts/utils/extract_ground_truth.sh

# Investigate stop-loss issues
python scripts/utils/investigate_sl_issue.py
```

---

**Last Updated:** December 15, 2025
