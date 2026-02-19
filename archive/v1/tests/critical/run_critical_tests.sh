#!/bin/bash
#
# Critical Path Test Runner
#
# Runs all money-critical tests and generates detailed reports.
# These tests MUST pass before deploying any changes to production.
#
# Usage:
#   ./run_critical_tests.sh           # Run all critical tests
#   ./run_critical_tests.sh --fast    # Run without coverage report
#   ./run_critical_tests.sh --verbose # Run with maximum verbosity
#

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Parse arguments
FAST_MODE=false
VERBOSE_MODE=false

for arg in "$@"; do
    case $arg in
        --fast)
            FAST_MODE=true
            shift
            ;;
        --verbose)
            VERBOSE_MODE=true
            shift
            ;;
        --help)
            echo "Usage: $0 [--fast] [--verbose]"
            echo ""
            echo "Options:"
            echo "  --fast     Skip coverage report (faster execution)"
            echo "  --verbose  Run with maximum verbosity"
            echo "  --help     Show this help message"
            exit 0
            ;;
    esac
done

# Change to project root
cd "$PROJECT_ROOT"

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  BotTrader Critical Path Test Suite${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${YELLOW}📋 Running money-critical tests...${NC}"
echo ""

# Check if pytest is installed
if ! command -v pytest &> /dev/null; then
    echo -e "${RED}❌ ERROR: pytest not found${NC}"
    echo "Please install pytest: pip install pytest pytest-asyncio"
    exit 1
fi

# Build pytest command
PYTEST_CMD="pytest tests/critical/ -m critical"

if [ "$VERBOSE_MODE" = true ]; then
    PYTEST_CMD="$PYTEST_CMD -vv"
else
    PYTEST_CMD="$PYTEST_CMD -v"
fi

# Add coverage if not in fast mode
if [ "$FAST_MODE" = false ]; then
    PYTEST_CMD="$PYTEST_CMD --cov=. --cov-report=term --cov-report=html"
fi

# Add color output
PYTEST_CMD="$PYTEST_CMD --color=yes"

# Run tests
echo -e "${BLUE}Running command:${NC} $PYTEST_CMD"
echo ""

if $PYTEST_CMD; then
    EXIT_CODE=0
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}✅ All critical tests PASSED${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo ""

    if [ "$FAST_MODE" = false ]; then
        echo -e "${BLUE}📊 Coverage report generated: htmlcov/index.html${NC}"
        echo ""
    fi

    echo -e "${GREEN}✅ System is ready for deployment${NC}"
    echo ""
else
    EXIT_CODE=1
    echo ""
    echo -e "${RED}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${RED}❌ CRITICAL TESTS FAILED${NC}"
    echo -e "${RED}═══════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${RED}⚠️  DO NOT DEPLOY TO PRODUCTION${NC}"
    echo -e "${YELLOW}Please fix failing tests before deploying.${NC}"
    echo ""
fi

# Summary
echo -e "${BLUE}Test Categories Covered:${NC}"
echo "  • FIFO Allocation (tax reporting)"
echo "  • Order Validation (risk management)"
echo "  • Stop Loss Logic (loss prevention)"
echo "  • P&L Calculation (financial accuracy)"
echo ""

exit $EXIT_CODE
