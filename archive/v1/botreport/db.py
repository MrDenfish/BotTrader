from __future__ import annotations
from sqlalchemy import create_engine
from .config import load_db_config

def get_sa_engine():
    cfg = load_db_config()
    if cfg.url:
        return create_engine(cfg.url, pool_pre_ping=True)
    dsn = f"postgresql+pg8000://{cfg.user}:{cfg.password}@{cfg.host}:{cfg.port}/{cfg.name}"
    return create_engine(dsn, pool_pre_ping=True)