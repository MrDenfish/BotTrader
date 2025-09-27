# Common/db_url.py
import os, urllib.parse
from urllib.parse import urlparse

ASYNC_DRIVER = "postgresql+asyncpg://"

def normalize_driver(url: str) -> str:
    # Ensure async driver; preserve path/query/fragment
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", ASYNC_DRIVER, 1)
    if not url.startswith(ASYNC_DRIVER):
        # accept already-correct async URL or other schemes
        return ASYNC_DRIVER + url.split("://", 1)[1] if "://" in url else url
    return url

def percent_encode(s: str) -> str:
    # Encode username/password safely
    return urllib.parse.quote_plus(s or "")



def build_asyncpg_url_from_env() -> str:
    """
    Precedence:
      1) DATABASE_URL (normalized to async driver)
      2) DB_* pieces (DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD)

    Note: do NOT append sslmode here; SSL is configured via connect_args for asyncpg.
    """
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return normalize_driver(env_url)

    # Default host depends on context: 'db' in Docker, 127.0.0.1 otherwise
    in_docker = os.getenv("IN_DOCKER", "false").lower() == "true"
    default_host = "db" if in_docker else "127.0.0.1"

    host = os.getenv("DB_HOST", default_host)
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "bot_trader_db")
    user = percent_encode(os.getenv("DB_USER", "bot_user"))
    pwd  = percent_encode(os.getenv("DB_PASSWORD", ""))

    return f"{ASYNC_DRIVER}{user}:{pwd}@{host}:{port}/{name}"

