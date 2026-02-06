"""
StrategyEngine ABC - interface for engines that drive strategy plugins.

Engines handle:
- Data loading and indicator computation
- Bar iteration and timeframe boundary detection
- Fill simulation (backtest) or real fill routing (production)
- Results collection and reporting
"""

from abc import ABC, abstractmethod
from typing import Optional

from strategies.core.base import StrategyPlugin


class StrategyEngine(ABC):
    """
    Abstract engine that drives a StrategyPlugin.

    Subclasses: BacktestEngine, ProductionAdapter
    """

    @abstractmethod
    def load(self, symbols: list[str], **kwargs) -> None:
        """Load data for the given symbols."""

    @abstractmethod
    def run(self) -> dict:
        """
        Run the engine (backtest loop or production adapter start).

        Returns:
            Results dict (strategy metrics, trade list, etc.)
        """

    @abstractmethod
    def get_strategy(self) -> StrategyPlugin:
        """Return the active strategy plugin."""

    @abstractmethod
    def get_results(self) -> dict:
        """Return results after run() completes."""
