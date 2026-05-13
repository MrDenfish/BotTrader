
import asyncio
import time
import datetime
from typing import Callable

async def periodic_runner(task_fn: Callable, interval: float, name: str = "PeriodicTask"):
    try:
        while True:
            start = time.monotonic()
            try:
                await task_fn()
            except Exception as e:
                print(f"❌ {name} error: {e}", exc_info=True)
            elapsed = time.monotonic() - start
            await asyncio.sleep(max(0, interval - elapsed))
    except asyncio.CancelledError:
        print(f"⚠️ {name} task was cancelled.")


