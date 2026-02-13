"""Signal comparison observer.

Logs v2 signals in JSONL format matching v1's score_log.jsonl structure.
Enables side-by-side comparison between v1 and v2 signal outputs.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone
from typing import Any

from v2.core import registry
from v2.core.interfaces import Observer
from v2.core.types import SignalEvent


@registry.plugin("observer", "signal_comparison")
class SignalComparisonObserver(Observer):
    """Logs v2 signals to JSONL for comparison with v1 score_log.jsonl."""

    name = "signal_comparison"

    def __init__(
        self,
        event_bus=None,
        log_path: str | None = None,
        backup_count: int = 7,
        **kwargs: Any,
    ) -> None:
        path = log_path or os.environ.get(
            "V2_SCORE_LOG_PATH", "logs/v2_score_log.jsonl"
        )
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

        self._logger = logging.getLogger("v2.signal_comparison")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

        if not self._logger.handlers:
            handler = logging.handlers.TimedRotatingFileHandler(
                path,
                when="midnight",
                backupCount=backup_count,
                utc=True,
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)

    def on_event(self, event: Any) -> None:
        if not isinstance(event, SignalEvent):
            return

        sig = event.signal
        record = {
            "ts": sig.timestamp.isoformat() if sig.timestamp else datetime.now(timezone.utc).isoformat(),
            "symbol": sig.symbol,
            "price": sig.price,
            "action": sig.direction.value,
            "trigger": sig.reason,
            "buy_score": sig.metadata.get("buy_score"),
            "sell_score": sig.metadata.get("sell_score"),
            "strategy": event.strategy_name,
            "version": "v2",
            "metadata": sig.metadata,
        }
        self._logger.info(json.dumps(record, default=str))
        for h in self._logger.handlers:
            h.flush()
