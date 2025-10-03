# bottrader/debug_config.py
import asyncio, faulthandler, logging, os, signal
from typing import TextIO
from .debug_env import env_bool, env_int, env_float, env_str

_DEFAULT_STACK_LOG = "/tmp/py-stacks.log"

class DebugToggles:
    # Runtime
    AIO_DEBUG          = env_bool("BT_DEBUG_AIO", False)
    LOG_LEVEL          = env_str("BT_LOG_LEVEL", "INFO")
    ASYNCIO_LOG_LEVEL  = env_str("BT_ASYNCIO_LOG_LEVEL", "WARNING")

    # Watchdog
    WATCHDOG_ENABLED   = env_bool("BT_WATCHDOG", True)
    WATCHDOG_INTERVAL  = env_float("BT_WATCHDOG_INTERVAL", 1.0)    # seconds
    WATCHDOG_THRESHOLD = env_int("BT_WATCHDOG_THRESHOLD_MS", 300)  # ms
    WATCHDOG_DUMP_ON_STALL = env_bool("BT_WATCHDOG_DUMP_ON_STALL", True)

    # Task census
    CENSUS_ENABLED     = env_bool("BT_TASK_CENSUS", False)
    CENSUS_INTERVAL    = env_int("BT_TASK_CENSUS_INTERVAL", 120)
    CENSUS_STACKS      = env_bool("BT_TASK_CENSUS_STACKS", False)

    # Auto dump
    AUTO_DUMP_SECS     = env_int("BT_AUTO_DUMP_SECS", 0)           # 0 = off
    STACK_LOG_PATH     = env_str("BT_STACK_LOG", _DEFAULT_STACK_LOG)

    # DB safety (used by your engine factory)
    DB_POOL_TIMEOUT_S  = env_int("BT_DB_POOL_TIMEOUT_S", 5)
    DB_STMT_TIMEOUT_MS = env_int("BT_DB_STMT_TIMEOUT_MS", 15000)
    DB_ECHO_POOL       = env_str("BT_DB_ECHO_POOL", "debug")       # or "info"/False

def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, DebugToggles.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logging.getLogger("asyncio").setLevel(
        getattr(logging, DebugToggles.ASYNCIO_LOG_LEVEL.upper(), logging.WARNING)
    )

def setup_stack_logging() -> TextIO:
    """
    Installs:
      - faulthandler to a file
      - SIGUSR2 handler to dump stacks on demand
      - optional periodic auto-dumps
    Returns an open file handle (keep a reference so it isn’t GC’d).
    """
    path = DebugToggles.STACK_LOG_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "a", buffering=1)  # line-buffered

    # All threads, always enabled
    faulthandler.enable(all_threads=True, file=fh)

    def _usr2(_signum, _frame):
        try:
            print("\n======== SIGNAL USR2 ========", file=fh)
            faulthandler.dump_traceback(file=fh, all_threads=True)
            print("======== END ========\n", file=fh)
        except Exception:
            pass

    # Only USR2 (USR1 can kill if not caught early)
    signal.signal(signal.SIGUSR2, _usr2)

    if DebugToggles.AUTO_DUMP_SECS > 0:
        faulthandler.dump_traceback_later(DebugToggles.AUTO_DUMP_SECS, repeat=True, file=fh)

    return fh

