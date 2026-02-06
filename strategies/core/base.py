"""
StrategyPlugin ABC - the interface all strategy plugins must implement.

A strategy plugin encapsulates:
- What it needs to see (timeframes, warmup bars)
- What it decides (evaluate → Optional[StrategySignal])
- How it reacts to fills (on_fill → Optional[StrategySignal])
- Its own state (get_state / load_state for checkpointing)

The engine owns:
- Data loading and indicator computation
- Fill simulation (backtest) or real fill routing (production)
- Bar iteration and timeframe boundary detection
"""

from abc import ABC, abstractmethod
from typing import Optional, Any

from strategies.core.types import (
    OHLCV, StrategySignal, FillNotification, StrategyMetrics
)


class StrategyPlugin(ABC):
    """
    Abstract base class for all trading strategy plugins.

    Strategies receive pre-computed bars and indicators, and emit
    signals. The engine translates signals into orders/fills.
    """

    # --- Identity ---

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy name, e.g. 'hybrid_4h_maker'."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Strategy version for tracking, e.g. '2.4'."""

    @property
    @abstractmethod
    def timeframes_required(self) -> list[str]:
        """
        Timeframes this strategy needs, e.g. ['1m', '4h', '1D'].
        The engine will ensure these are computed and aligned.
        """

    # --- Lifecycle ---

    def configure(self, config: Any) -> None:
        """
        Accept strategy-specific configuration.

        Each strategy has its own config shape (dataclass, dict, etc).
        Called once before start().
        """

    def warmup_bars(self) -> dict[str, int]:
        """
        Minimum bars per timeframe before signals are valid.

        Returns:
            e.g. {'4h': 200, '1D': 200} - engine skips until warmup met.
        """
        return {}

    def start(self) -> None:
        """Called once when the engine begins a session (backtest or live)."""

    def stop(self) -> None:
        """Called once when the engine ends a session."""

    # --- Core Decision ---

    @abstractmethod
    def evaluate(
        self,
        symbol: str,
        bar: OHLCV,
        indicators: dict[str, dict],
        context: dict
    ) -> Optional[StrategySignal]:
        """
        Core method: evaluate current bar and return a signal.

        Called once per bar per symbol at the base timeframe.

        Args:
            symbol: Trading pair, e.g. 'BTC-USD'
            bar: Current OHLCV bar
            indicators: Pre-computed indicators keyed by timeframe,
                        e.g. {'4h': {'atr_pct': 0.02, ...}, '1D': {'ema200': 95000, ...}}
            context: Engine context, e.g. {'is_4h_close': True, 'is_1d_close': False}

        Returns:
            StrategySignal if the strategy wants to act, None otherwise.
        """

    def on_fill(self, fill: FillNotification) -> Optional[StrategySignal]:
        """
        React to a fill notification from the engine.

        For strategies that manage multi-leg exits (TP1 → TP2 → runner),
        this is where exit transitions happen.

        For simple strategies (composite scoring), this is a no-op.

        Args:
            fill: Details of the executed fill.

        Returns:
            Follow-up signal (e.g. place next TP), or None.
        """
        return None

    # --- Observability ---

    def get_metrics(self) -> StrategyMetrics:
        """Return current performance metrics."""
        return StrategyMetrics()

    # --- State persistence ---

    def get_state(self) -> dict:
        """
        Serialize strategy state for checkpointing.

        Returns a dict that can be JSON-serialized and later
        passed to load_state() to restore.
        """
        return {}

    def load_state(self, state: dict) -> None:
        """Restore strategy state from a checkpoint."""
