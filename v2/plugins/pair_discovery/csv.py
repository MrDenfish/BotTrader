"""CSV pair discovery plugin.

Scans a directory of CSV files and extracts trading symbols from filenames.
Useful for backtesting: automatically discovers all available data instead
of requiring a hardcoded symbol list.

Filename conventions:
  BTC_USD.csv      -> BTC-USD
  BTC_USD_5m.csv   -> BTC-USD  (resolution suffix stripped)
  AVAX_USD.csv     -> AVAX-USD
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.interfaces import PairDiscovery

logger = logging.getLogger(__name__)

# Matches: BASE_QUOTE with optional _resolution suffix before .csv
_FILENAME_RE = re.compile(r"^([A-Z0-9]+)_([A-Z0-9]+?)(?:_\d+[mhd])?\.csv$", re.IGNORECASE)


@registry.plugin("pair_discovery", "csv")
class CsvPairDiscovery(PairDiscovery):
    """Discovers trading symbols from CSV filenames in a directory."""

    name = "csv"

    def __init__(
        self,
        event_bus: EventBus | None = None,
        **kwargs: Any,
    ) -> None:
        self._bus = event_bus
        self._data_dir: Path | None = None

    def configure(self, config: Any) -> None:
        if not isinstance(config, dict):
            return
        raw = config.get("data_dir", "backtest/data")
        self._data_dir = Path(raw)

    async def discover(self) -> list[str]:
        if self._data_dir is None:
            self._data_dir = Path("backtest/data")

        if not self._data_dir.is_dir():
            logger.warning("CSV data directory not found: %s", self._data_dir)
            return []

        symbols: set[str] = set()
        for f in self._data_dir.iterdir():
            m = _FILENAME_RE.match(f.name)
            if m:
                base, quote = m.group(1).upper(), m.group(2).upper()
                symbols.add(f"{base}-{quote}")

        result = sorted(symbols)
        logger.info("CSV pair discovery: found %d symbols in %s", len(result), self._data_dir)
        return result

    async def start(self) -> None:
        pass  # Static files — no periodic refresh needed

    async def stop(self) -> None:
        pass
