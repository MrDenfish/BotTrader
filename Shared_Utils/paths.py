# paths.py
from pathlib import Path
import os

def resolve_runtime_paths(is_docker: bool):
    if is_docker:
        default_data  = Path("/app/data")
        default_cache = Path("/app/cache")
        default_logs  = Path("/app/logs")
    else:
        base          = Path.cwd() / ".bottrader"
        default_data  = base
        default_cache = base / "cache"
        default_logs  = base / "logs"

    data  = Path(os.getenv("BOTTRADER_DATA_DIR",  default_data))
    cache = Path(os.getenv("BOTTRADER_CACHE_DIR", default_cache))
    logs  = Path(os.getenv("BOTTRADER_LOG_DIR",   default_logs))
    for d in (data, cache, logs): d.mkdir(parents=True, exist_ok=True)
    return data, cache, logs
