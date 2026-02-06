# Strategy Plugin Interface

**Date**: 2026-02-06
**Status**: Draft
**Related**: [Plugin Architecture Overview](../README.md)

## Overview

Defines the contract that all trading strategy plugins must implement to integrate with the BotTrader engine.

## Interface (Draft)

```python
class StrategyPlugin(ABC):
    """Base interface for all trading strategy plugins."""

    @abstractmethod
    def name(self) -> str:
        """Unique strategy identifier."""

    @abstractmethod
    def configure(self, config: dict) -> None:
        """Initialize strategy with configuration parameters."""

    @abstractmethod
    def on_candle(self, candle: CandleData) -> Optional[Signal]:
        """Process a new candle and optionally generate a trading signal."""

    @abstractmethod
    def on_fill(self, fill: FillData) -> None:
        """Handle order fill notification."""

    @abstractmethod
    def get_state(self) -> dict:
        """Return serializable state for checkpointing."""

    @abstractmethod
    def load_state(self, state: dict) -> None:
        """Restore from checkpointed state."""
```

## TODO

- Define `CandleData`, `Signal`, `FillData` data classes
- Define lifecycle hooks (start, stop, pause)
- Define risk management integration points
- Define configuration schema validation
- Determine state serialization format
