import os
import json
import asyncio
import asyncpg

from sqlalchemy import text
from typing import Optional, Any
from sqlalchemy.orm import sessionmaker
from contextlib import asynccontextmanager
from sqlalchemy.exc import OperationalError, DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

class _NoopLogger:
    def debug(self,*a,**k): pass
    info = debug; warning = debug; error = debug; exception = debug


class DatabaseSessionManager:
    """Creates the async engine, yields sessions, runs a one-time schema bootstrap.
       Includes app-specific helpers for now (can be moved to SharedDataManager later)."""

    _instance = None

    @classmethod
    def get_instance(
            cls,
            dsn: str,
            logger: Optional[Any] = None,
            custom_json_decoder: Optional[type] = None,
            **engine_kw,
    ):
        if cls._instance is None:
            cls._instance = cls(dsn, logger=logger, custom_json_decoder=custom_json_decoder, **engine_kw)
        return cls._instance

    def __init__(self, dsn: str, logger: Optional[Any] = None, custom_json_decoder: Optional[type] = None, **engine_kw):
        # Logger is duck-typed (must have .debug/.info/.warning/.error/.exception)
        self.logger = logger or _NoopLogger()

        # Optional JSON decoder class for SharedData payloads
        # (json.loads accepts a Decoder CLASS via the "cls=" parameter)
        self.custom_json_decoder = custom_json_decoder or json.JSONDecoder

        # Normalize DSN to async driver if needed
        if dsn.startswith("postgres://"):
            dsn = dsn.replace("postgres://", "postgresql+asyncpg://", 1)
        elif dsn.startswith("postgresql://") and "+asyncpg" not in dsn:
            dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

        # Defaults (caller can override via **engine_kw)
        defaults = dict(
            echo=False,
            pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
            pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "10")),
            pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "300")),  # 5m
            pool_pre_ping=True,
            future=True,
            connect_args={
                "timeout": float(os.getenv("DB_CONNECT_TIMEOUT", "5")),
                "command_timeout": float(os.getenv("DB_COMMAND_TIMEOUT", "30")),
                "server_settings": {
                    "application_name": os.getenv("DB_APP_NAME", "bottrader"),
                    "statement_timeout": os.getenv("DB_STATEMENT_TIMEOUT_MS", "60000"),
                    "tcp_keepalives_idle": os.getenv("DB_TCP_KEEPALIVES_IDLE", "60"),
                    "tcp_keepalives_interval": os.getenv("DB_TCP_KEEPALIVES_INTERVAL", "20"),
                    "tcp_keepalives_count": os.getenv("DB_TCP_KEEPALIVES_COUNT", "3"),
                },
            },
        )
        for k, v in defaults.items():
            engine_kw.setdefault(k, v)

        # Engine + session factory
        self.engine = create_async_engine(dsn, **engine_kw)
        self._async_session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False, class_=AsyncSession
        )

        # One-time bootstrap guards
        self._schema_lock = asyncio.Lock()
        self._schema_ready = False

        # ---------- bootstrap / session ----------

    async def _ensure_schema_once(self):
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            try:
                # Import lazily so environments without this module don't crash at import time
                from .bootstrap_schema import ensure_trade_provenance_schema  # type: ignore
            except Exception:
                ensure_trade_provenance_schema = None

            if ensure_trade_provenance_schema:
                try:
                    await ensure_trade_provenance_schema(self.engine)
                except Exception as e:
                    self.logger.debug("Schema bootstrap failed/skipped: %s", e)
            self._schema_ready = True

    @asynccontextmanager
    async def async_session(self):
        await self._ensure_schema_once()
        async with self._async_session_factory() as session:
            yield session

        # ---------- retry helper ----------

    @staticmethod
    def is_retryable_db_error(e: Exception) -> bool:
        RETRYABLE_SNIPPETS = (
            "ConnectionDoesNotExistError",
            "connection was closed",
            "server closed the connection",
            "could not receive data from server",
            "terminating connection due to administrator command",
            "Connection reset by peer",
            "transport closed",
        )
        s = str(e)
        return isinstance(e, (ConnectionError, OSError, OperationalError, DBAPIError, asyncpg.PostgresError)) \
            and any(sn in s for sn in RETRYABLE_SNIPPETS)

    @staticmethod
    def db_retry_once(func):
        import functools
        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs):
            try:
                return await func(self, *args, **kwargs)
            except Exception as e:
                if not DatabaseSessionManager.is_retryable_db_error(e):
                    raise
                try:
                    await self.engine.dispose()
                except Exception:
                    pass
                await asyncio.sleep(float(os.getenv("DB_RETRY_BACKOFF_SEC", "0.5")))
                return await func(self, *args, **kwargs)

        return wrapper

    # ---------- light engine warm-up ----------

    async def initialize(self) -> None:
        """Warm the pool and verify connectivity (single retry)."""
        last_exc = None
        for attempt in (1, 2):
            try:
                async with self.async_session() as s:
                    await s.execute(text("SELECT 1"))
                return
            except (OSError, ConnectionError, OperationalError, DBAPIError, asyncpg.PostgresError) as e:
                last_exc = e
                try:
                    await self.engine.dispose()
                except Exception:
                    pass
                if attempt == 1:
                    await asyncio.sleep(0.75)
                    continue
                break
        raise last_exc  # surface the original error

    # ---------- optional convenience API (app-specific; OK for now) ----------

    async def initialize_schema(self):
        raise RuntimeError("initialize_schema() moved to SharedDataManager. Call shared_data_manager.initialize_schema().")

    @property
    def async_session_factory(self):
        raise RuntimeError("Do not access session factory directly. Use async_session().")

    async def get_active_connection_count(self) -> int:
        try:
            async with self.async_session() as session:
                result = await session.execute(
                    text("SELECT COUNT(*) FROM pg_stat_activity WHERE datname = current_database();")
                )
                return int(result.scalar_one())
        except Exception as e:
            self.logger.warning("⚠️ Failed to get active DB connections: %s", e, exc_info=True)
            return -1


    async def disconnect(self):
        """Close the SQLAlchemy database engine (optional for graceful shutdown)."""
        try:
            if self.engine:
                await self.engine.dispose()
                self.logger.info("✅ SQLAlchemy engine disposed successfully.")
        except Exception as e:
            self.logger.error(f"❌ Error while disposing SQLAlchemy engine: {e}", exc_info=True)


