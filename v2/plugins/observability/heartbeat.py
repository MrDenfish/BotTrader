"""Heartbeat observer.

Touches a file on every event (debounced to configurable interval).
Docker health checks monitor this file's mtime to detect stalls.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from v2.core import registry
from v2.core.interfaces import Observer


@registry.plugin("observer", "heartbeat")
class HeartbeatObserver(Observer):
    """Touches a heartbeat file periodically for Docker health checks."""

    name = "heartbeat"

    def __init__(
        self,
        event_bus=None,
        path: str = "/app/logs/v2/heartbeat",
        interval: float = 10.0,
        **kwargs: Any,
    ) -> None:
        self._path = Path(path)
        self._interval = interval
        self._last_touch: float = 0.0

    def on_event(self, event: Any) -> None:
        now = time.monotonic()
        if now - self._last_touch < self._interval:
            return
        self._last_touch = now
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch()
