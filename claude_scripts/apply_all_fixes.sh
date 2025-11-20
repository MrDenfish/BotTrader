#!/bin/bash

# ================================================
# CLANKER Position Bug - Master Fix Script
# ================================================
# This script applies all fixes in the correct order
# ================================================

set -e  # Exit on error

echo "========================================"
echo "CLANKER Position Bug Fix - Master Script"
echo "========================================"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

DOCKER_COMPOSE_FILE="docker-compose.aws.yml"
DB_USER="bot_user"
DB_NAME="bot_trader_db"

# Check if docker-compose file exists
if [ ! -f "$DOCKER_COMPOSE_FILE" ]; then
    echo -e "${RED}ERROR: $DOCKER_COMPOSE_FILE not found!${NC}"
    echo "Please run this script from your BotTrader directory"
    exit 1
fi

echo -e "${YELLOW}Step 0: Investigation (Understanding the problem)${NC}"
echo "Running diagnostic queries..."
docker compose -f $DOCKER_COMPOSE_FILE exec -T db psql -U $DB_USER -d $DB_NAME < investigate_bug.sql > investigation_results.txt 2>&1
echo -e "${GREEN}✓ Investigation results saved to: investigation_results.txt${NC}"
echo ""
echo "Press Enter to review the results, or Ctrl+C to abort..."
read

echo ""
echo -e "${YELLOW}Step 1: Quick Data Fix (Correcting existing records)${NC}"
echo "This will fix all FILLED buy orders with incorrect remaining_size..."
echo "Press Enter to continue, or Ctrl+C to abort..."
read

docker compose -f $DOCKER_COMPOSE_FILE exec -T db psql -U $DB_USER -d $DB_NAME < fix_remaining_size_bug.sql > fix_results.txt 2>&1
echo -e "${GREEN}✓ Data fix completed! Results saved to: fix_results.txt${NC}"
echo ""
echo "Quick check - showing fix results:"
tail -30 fix_results.txt
echo ""

echo -e "${YELLOW}Step 2: Update Views (Defensive fix for future data)${NC}"
echo "This will update report_trades and report_positions views..."
echo "Press Enter to continue, or Ctrl+C to abort..."
read

docker compose -f $DOCKER_COMPOSE_FILE exec -T db psql -U $DB_USER -d $DB_NAME < fix_report_views.sql > view_fix_results.txt 2>&1
echo -e "${GREEN}✓ Views updated! Results saved to: view_fix_results.txt${NC}"
echo ""

echo -e "${YELLOW}Step 3: Verification${NC}"
echo "Running verification queries..."
docker compose -f $DOCKER_COMPOSE_FILE exec -T db psql -U $DB_USER -d $DB_NAME << 'EOSQL' | tee verification_results.txt
-- Check CLANKER position
SELECT 
    'CLANKER Position (Should be ~0)' as check,
    symbol,
    position_qty,
    avg_entry_price,
    (position_qty * avg_entry_price) as notional_usd
FROM report_positions
WHERE symbol = 'CLANKER-USD';

-- Check top positions
SELECT 
    'Top 10 Positions' as check,
    symbol,
    position_qty,
    avg_entry_price,
    (position_qty * avg_entry_price) as notional_usd
FROM report_positions
ORDER BY ABS(position_qty * avg_entry_price) DESC
LIMIT 10;

-- Check total notional
SELECT 
    'Total Portfolio Notional (Should be ~$192)' as check,
    SUM(ABS(position_qty * avg_entry_price)) as total_notional_usd,
    COUNT(DISTINCT symbol) as num_positions
FROM report_positions;
EOSQL

echo ""
echo -e "${GREEN}✓ Verification complete!${NC}"
echo ""

echo -e "${YELLOW}Step 4: Test Report Generation${NC}"
echo "Generating test report to verify fixes..."
docker compose -f $DOCKER_COMPOSE_FILE run --rm report-job python -m botreport --hours 24 > test_report.txt 2>&1
echo -e "${GREEN}✓ Test report generated: test_report.txt${NC}"
echo ""

echo "========================================"
echo -e "${GREEN}ALL FIXES COMPLETED SUCCESSFULLY!${NC}"
echo "========================================"
echo ""
echo "Summary of files created:"
echo "  - investigation_results.txt  : Initial problem analysis"
echo "  - fix_results.txt           : Data correction results"
echo "  - view_fix_results.txt      : View update results"
echo "  - verification_results.txt  : Final verification"
echo "  - test_report.txt           : Sample report"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo "1. Review verification_results.txt to confirm:"
echo "   - CLANKER position is near 0"
echo "   - Total notional is around \$192 (not \$100k)"
echo "2. Review test_report.txt for the email report"
echo "3. Monitor next scheduled report to ensure fix persists"
echo ""
echo -e "${YELLOW}⚠️  IMPORTANT: Still need to fix the root cause in code!${NC}"
echo "Search for where 'remaining_size' is set in your Python code:"
echo "  grep -rn 'remaining_size.*=' --include='*.py' ."
echo ""
