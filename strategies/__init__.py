"""
BotTrader Strategy Plugin Architecture

Provides a unified interface for developing, backtesting, and running
trading strategies as interchangeable plugins.
"""

import sys
from pathlib import Path

# The backtest/ package has bare imports (e.g. 'from config_4h_hybrid import ...')
# that require backtest/ to be on sys.path. We add it here once so all
# strategy modules that import from backtest.* work correctly.
_backtest_dir = str(Path(__file__).resolve().parent.parent / 'backtest')
if _backtest_dir not in sys.path:
    sys.path.insert(0, _backtest_dir)

from strategies.core.base import StrategyPlugin
from strategies.core.types import (
    OHLCV, StrategySignal, SignalDirection,
    FillNotification, StrategyMetrics
)
from strategies.registry import StrategyRegistry

__all__ = [
    'StrategyPlugin',
    'OHLCV', 'StrategySignal', 'SignalDirection',
    'FillNotification', 'StrategyMetrics',
    'StrategyRegistry',
]
