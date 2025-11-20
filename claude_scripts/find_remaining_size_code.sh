#!/bin/bash

# ================================================
# Find where remaining_size is set in code
# ================================================
# Run this in your BotTrader directory
# ================================================

echo "========================================"
echo "Searching for remaining_size in Python code"
echo "========================================"
echo ""

SEARCH_DIR="${1:-.}"

echo "Search directory: $SEARCH_DIR"
echo ""

echo "1. Finding files with 'remaining_size' assignments:"
echo "---------------------------------------------------"
grep -rn "remaining_size.*=" --include="*.py" "$SEARCH_DIR" | \
    grep -v "test" | \
    grep -v ".pyc" | \
    grep -v "__pycache__" | \
    grep -v "#"
echo ""

echo "2. Finding TradeRecorder files:"
echo "---------------------------------------------------"
find "$SEARCH_DIR" -name "*recorder*.py" | grep -v __pycache__
echo ""

echo "3. Finding trade-related files:"
echo "---------------------------------------------------"
find "$SEARCH_DIR" -name "*trade*.py" | grep -v __pycache__ | grep -v test | head -20
echo ""

echo "4. Finding database INSERT/UPDATE with remaining_size:"
echo "---------------------------------------------------"
grep -rn "remaining_size" --include="*.py" "$SEARCH_DIR" | \
    grep -iE "insert|update" | \
    grep -v test | \
    grep -v ".pyc" | \
    head -20
echo ""

echo "5. Finding DataFrame/dict assignments with remaining_size:"
echo "---------------------------------------------------"
grep -rn "\['remaining_size'\].*=" --include="*.py" "$SEARCH_DIR" | \
    grep -v test | \
    head -20
echo ""

echo "6. Finding potential webhook/order handlers:"
echo "---------------------------------------------------"
find "$SEARCH_DIR" -name "*webhook*.py" -o -name "*order*.py" -o -name "*fill*.py" | \
    grep -v __pycache__ | \
    grep -v test
echo ""

echo "========================================"
echo "Search complete!"
echo "========================================"
echo ""
echo "Likely culprits to investigate:"
echo "  1. TradeRecorder.enqueue_trade() or similar"
echo "  2. Webhook message handler"
echo "  3. Order fill handler"
echo "  4. Database INSERT/UPDATE statements"
echo ""
echo "Look for code that does:"
echo "  remaining_size = order.get('filled_size')  # BUG!"
echo "  remaining_size = order.get('size')         # BUG!"
echo ""
echo "Should be:"
echo "  if order.get('status') == 'FILLED':"
echo "      remaining_size = 0  # or None"
echo "  else:"
echo "      remaining_size = order.get('remaining_size')"
echo ""
