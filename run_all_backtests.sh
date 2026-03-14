#!/bin/bash
# Run all 3 dataset backtests sequentially (hourly ATR + 5.5% floor)
cd /Users/Manny/Python_Projects/BotTrader

echo "=== Set A: Starting at $(date) ==="
python -m v2 --config v2/backtest_diagnostic.yaml > backtest/set_a_hourly_atr.log 2>&1
echo "=== Set A: Finished at $(date) ==="

echo "=== Set B: Starting at $(date) ==="
python -m v2 --config v2/backtest_diagnostic_set_b.yaml > backtest/set_b_hourly_atr.log 2>&1
echo "=== Set B: Finished at $(date) ==="

echo "=== Set C: Starting at $(date) ==="
python -m v2 --config v2/backtest_diagnostic_set_c.yaml > backtest/set_c_hourly_atr.log 2>&1
echo "=== Set C: Finished at $(date) ==="

echo "=== ALL DONE at $(date) ==="
