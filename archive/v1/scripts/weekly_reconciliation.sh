#!/bin/bash
#
# Weekly Exchange Reconciliation
#
# This script runs weekly to detect missing trades from the database
# by comparing against Coinbase exchange data.
#
# Schedule: Every Sunday at 2:00 AM
# Cron: 0 2 * * 0 /Users/Manny/Python_Projects/BotTrader/scripts/weekly_reconciliation.sh
#

set -e  # Exit on error

# Configuration
PROJECT_DIR="/Users/Manny/Python_Projects/BotTrader"
LOG_DIR="${PROJECT_DIR}/.bottrader/logs"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="${LOG_DIR}/reconciliation_${TIMESTAMP}.log"

# Ensure log directory exists
mkdir -p "${LOG_DIR}"

# Log start
echo "========================================" | tee -a "${LOG_FILE}"
echo "Weekly Reconciliation Started" | tee -a "${LOG_FILE}"
echo "Time: $(date)" | tee -a "${LOG_FILE}"
echo "========================================" | tee -a "${LOG_FILE}"
echo "" | tee -a "${LOG_FILE}"

# Change to project directory
cd "${PROJECT_DIR}"

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    echo "Activating virtual environment..." | tee -a "${LOG_FILE}"
    source venv/bin/activate
elif [ -d ".venv" ]; then
    echo "Activating virtual environment..." | tee -a "${LOG_FILE}"
    source .venv/bin/activate
fi

# Get the latest FIFO version
echo "Determining latest FIFO version..." | tee -a "${LOG_FILE}"
LATEST_VERSION=$(python3 -c "
import asyncio
import os
from Config.config_manager import CentralConfig
from database_manager.database_session_manager import DatabaseSessionManager
from Shared_Utils.logging_manager import LoggerManager
from sqlalchemy import text

async def get_latest_version():
    config = CentralConfig(is_docker=False)
    dsn = config.database_url.replace('postgresql://', 'postgresql+asyncpg://')

    logger_manager = LoggerManager({'log_level': 'WARNING'})
    logger = logger_manager.get_logger('shared_logger')

    db = DatabaseSessionManager(dsn, logger=logger, echo=False, pool_size=2, max_overflow=2, pool_timeout=10, pool_recycle=300, pool_pre_ping=True, future=True)
    await db.initialize()

    async with db.async_session() as session:
        result = await session.execute(text('SELECT MAX(allocation_version) FROM fifo_allocations'))
        version = result.fetchone()[0]

    return version if version else 1

print(asyncio.run(get_latest_version()))
" 2>>"${LOG_FILE}")

echo "Latest version: ${LATEST_VERSION}" | tee -a "${LOG_FILE}"
echo "" | tee -a "${LOG_FILE}"

# Run Tier 1 reconciliation (read-only check)
echo "Running Tier 1 reconciliation..." | tee -a "${LOG_FILE}"
python -m scripts.reconcile_with_exchange --version "${LATEST_VERSION}" --tier 1 2>&1 | tee -a "${LOG_FILE}"

RECONCILE_EXIT_CODE=${PIPESTATUS[0]}

echo "" | tee -a "${LOG_FILE}"
echo "========================================" | tee -a "${LOG_FILE}"

if [ ${RECONCILE_EXIT_CODE} -eq 0 ]; then
    echo "✅ Reconciliation completed successfully" | tee -a "${LOG_FILE}"

    # Check if any missing buys were found
    if grep -q "Missing BUY orders found: 0" "${LOG_FILE}"; then
        echo "✅ No missing trades detected - database is in sync" | tee -a "${LOG_FILE}"
    else
        echo "⚠️  Missing trades detected - review required" | tee -a "${LOG_FILE}"
        echo "   Check log file: ${LOG_FILE}" | tee -a "${LOG_FILE}"

        # Optional: Send alert (email, Slack, etc.)
        # Uncomment and configure as needed:
        # echo "Sending alert..." | tee -a "${LOG_FILE}"
        # python -m scripts.send_alert --type reconciliation --log "${LOG_FILE}"
    fi
else
    echo "❌ Reconciliation failed with exit code ${RECONCILE_EXIT_CODE}" | tee -a "${LOG_FILE}"
    echo "   Check log file: ${LOG_FILE}" | tee -a "${LOG_FILE}"

    # Optional: Send alert for failure
    # python -m scripts.send_alert --type error --log "${LOG_FILE}"
fi

echo "Time: $(date)" | tee -a "${LOG_FILE}"
echo "========================================" | tee -a "${LOG_FILE}"

# Cleanup old logs (keep last 12 weeks = ~3 months)
echo "" | tee -a "${LOG_FILE}"
echo "Cleaning up old logs (keeping last 12 weeks)..." | tee -a "${LOG_FILE}"
find "${LOG_DIR}" -name "reconciliation_*.log" -type f -mtime +84 -delete

echo "✅ Cleanup complete" | tee -a "${LOG_FILE}"
echo "" | tee -a "${LOG_FILE}"

# Keep symlink to latest log
ln -sf "${LOG_FILE}" "${LOG_DIR}/reconciliation_latest.log"

echo "Log file: ${LOG_FILE}" | tee -a "${LOG_FILE}"
echo "Latest log symlink: ${LOG_DIR}/reconciliation_latest.log"

exit ${RECONCILE_EXIT_CODE}
