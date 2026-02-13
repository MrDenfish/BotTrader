"""PostgreSQL storage adapter.

Production-grade persistence using asyncpg for async PostgreSQL access.
Ported from database_manager/database_session_manager.py and
SharedDataManager/trade_recorder.py.

Tables are auto-created on connect if they don't exist.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from v2.core import registry
from v2.core.interfaces import StorageAdapter
from v2.core.types import Fill, Order, Position, Side, OrderStatus, OrderType

logger = logging.getLogger(__name__)


@registry.plugin("storage", "postgres")
class PostgresStorage(StorageAdapter):
    """PostgreSQL storage adapter using asyncpg.

    Provides:
    - Fill and order recording
    - Position queries
    - Trade history queries
    - Key-value state persistence
    - Auto table creation on connect
    """

    name = "postgres"

    def __init__(
        self,
        event_bus=None,
        dsn: str | None = None,
        dsn_env: str = "DATABASE_URL",
        pool_size: int = 5,
        **kwargs: Any,
    ) -> None:
        self._dsn = dsn or os.environ.get(dsn_env, "")
        self._clean_dsn = ""  # Set in connect() after stripping +asyncpg
        self._pool_size = pool_size
        self._pool = None  # asyncpg.Pool

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        import asyncpg

        if not self._dsn:
            raise ValueError(
                "PostgreSQL DSN not configured. Set DATABASE_URL env or pass dsn="
            )

        dsn = self._dsn
        if "+asyncpg" in dsn:
            dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        self._clean_dsn = dsn

        for attempt in range(1, 4):
            try:
                self._pool = await asyncpg.create_pool(
                    dsn,
                    min_size=1,
                    max_size=self._pool_size,
                    command_timeout=30,
                )
                break
            except Exception:
                if attempt == 3:
                    raise
                wait = 2 ** attempt
                logger.warning(
                    "PostgreSQL connect attempt %d/3 failed, retrying in %ds",
                    attempt, wait,
                )
                await asyncio.sleep(wait)

        await self._create_tables()
        logger.info("PostgreSQL connected (pool_size=%d)", self._pool_size)

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("PostgreSQL disconnected")

    async def _reconnect(self) -> None:
        """Close broken pool and create a new one."""
        import asyncpg

        logger.warning("PostgreSQL reconnecting...")
        try:
            if self._pool:
                await self._pool.close()
        except Exception:
            pass
        self._pool = await asyncpg.create_pool(
            self._clean_dsn,
            min_size=1,
            max_size=self._pool_size,
            command_timeout=30,
        )
        logger.info("PostgreSQL reconnected")

    # ------------------------------------------------------------------
    # Record
    # ------------------------------------------------------------------

    async def record_fill(self, fill: Fill) -> None:
        for attempt in range(1, 3):
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        """INSERT INTO v2_fills
                           (fill_id, order_id, symbol, side, price, qty, fee,
                            fee_currency, is_maker, timestamp, metadata)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                           ON CONFLICT (fill_id) DO NOTHING""",
                        fill.fill_id,
                        fill.order_id,
                        fill.symbol,
                        fill.side.value,
                        float(fill.price),
                        float(fill.qty),
                        float(fill.fee),
                        fill.fee_currency,
                        fill.is_maker,
                        fill.timestamp,
                        json.dumps(fill.metadata, default=str),
                    )
                return
            except Exception:
                logger.warning(
                    "record_fill attempt %d/2 failed for %s",
                    attempt, fill.fill_id, exc_info=True,
                )
                if attempt < 2:
                    await self._reconnect()

    async def record_order(self, order: Order) -> None:
        await self._pool.execute(
            """INSERT INTO v2_orders
               (order_id, symbol, side, order_type, price, qty, status,
                timestamp, metadata)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
               ON CONFLICT (order_id) DO UPDATE SET
                 status = EXCLUDED.status,
                 metadata = EXCLUDED.metadata""",
            order.order_id,
            order.symbol,
            order.side.value,
            order.order_type.value,
            float(order.price),
            float(order.qty),
            order.status.value,
            order.timestamp,
            json.dumps(order.metadata, default=str),
        )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        if symbol:
            rows = await self._pool.fetch(
                "SELECT * FROM v2_positions WHERE symbol = $1", symbol,
            )
        else:
            rows = await self._pool.fetch("SELECT * FROM v2_positions")
        return [self._row_to_position(r) for r in rows]

    async def get_trades(
        self, symbol: str | None = None, since: datetime | None = None
    ) -> list[Fill]:
        query = "SELECT * FROM v2_fills WHERE TRUE"
        params: list = []
        idx = 1

        if symbol:
            query += f" AND symbol = ${idx}"
            params.append(symbol)
            idx += 1
        if since:
            query += f" AND timestamp >= ${idx}"
            params.append(since)
            idx += 1

        query += " ORDER BY timestamp ASC"
        rows = await self._pool.fetch(query, *params)
        return [self._row_to_fill(r) for r in rows]

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    async def save_state(self, key: str, state: dict) -> None:
        await self._pool.execute(
            """INSERT INTO v2_state (key, value, updated_at)
               VALUES ($1, $2, $3)
               ON CONFLICT (key) DO UPDATE SET
                 value = EXCLUDED.value,
                 updated_at = EXCLUDED.updated_at""",
            key,
            json.dumps(state, default=str),
            datetime.now(timezone.utc),
        )

    async def load_state(self, key: str) -> dict | None:
        row = await self._pool.fetchrow(
            "SELECT value FROM v2_state WHERE key = $1", key,
        )
        if row:
            return json.loads(row["value"])
        return None

    # ------------------------------------------------------------------
    # Table creation
    # ------------------------------------------------------------------

    async def _create_tables(self) -> None:
        """Create v2 tables if they don't exist."""
        await self._pool.execute("""
            CREATE TABLE IF NOT EXISTS v2_fills (
                fill_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                price DOUBLE PRECISION NOT NULL,
                qty DOUBLE PRECISION NOT NULL,
                fee DOUBLE PRECISION NOT NULL DEFAULT 0,
                fee_currency TEXT DEFAULT 'USD',
                is_maker BOOLEAN DEFAULT FALSE,
                timestamp TIMESTAMPTZ NOT NULL,
                metadata JSONB DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_v2_fills_symbol
                ON v2_fills (symbol);
            CREATE INDEX IF NOT EXISTS idx_v2_fills_timestamp
                ON v2_fills (timestamp);
            CREATE INDEX IF NOT EXISTS idx_v2_fills_order_id
                ON v2_fills (order_id);
        """)

        await self._pool.execute("""
            CREATE TABLE IF NOT EXISTS v2_orders (
                order_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                price DOUBLE PRECISION NOT NULL,
                qty DOUBLE PRECISION NOT NULL,
                status TEXT NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                metadata JSONB DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_v2_orders_symbol
                ON v2_orders (symbol);
            CREATE INDEX IF NOT EXISTS idx_v2_orders_status
                ON v2_orders (status);
        """)

        await self._pool.execute("""
            CREATE TABLE IF NOT EXISTS v2_positions (
                symbol TEXT PRIMARY KEY,
                qty DOUBLE PRECISION NOT NULL DEFAULT 0,
                avg_entry_price DOUBLE PRECISION NOT NULL DEFAULT 0,
                cost_basis DOUBLE PRECISION NOT NULL DEFAULT 0,
                unrealized_pnl DOUBLE PRECISION DEFAULT 0,
                realized_pnl DOUBLE PRECISION DEFAULT 0,
                entry_time TIMESTAMPTZ
            );
        """)

        await self._pool.execute("""
            CREATE TABLE IF NOT EXISTS v2_state (
                key TEXT PRIMARY KEY,
                value JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        logger.debug("PostgreSQL tables verified/created")

    # ------------------------------------------------------------------
    # Row converters
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_fill(row) -> Fill:
        metadata = {}
        if row["metadata"]:
            try:
                metadata = json.loads(row["metadata"]) if isinstance(
                    row["metadata"], str
                ) else row["metadata"]
            except (json.JSONDecodeError, TypeError):
                pass

        return Fill(
            fill_id=row["fill_id"],
            order_id=row["order_id"],
            symbol=row["symbol"],
            side=Side(row["side"]),
            price=Decimal(str(row["price"])),
            qty=Decimal(str(row["qty"])),
            fee=Decimal(str(row["fee"])),
            fee_currency=row["fee_currency"],
            is_maker=row["is_maker"],
            timestamp=row["timestamp"],
            metadata=metadata,
        )

    @staticmethod
    def _row_to_position(row) -> Position:
        return Position(
            symbol=row["symbol"],
            qty=Decimal(str(row["qty"])),
            avg_entry_price=Decimal(str(row["avg_entry_price"])),
            cost_basis=Decimal(str(row["cost_basis"])),
            unrealized_pnl=Decimal(str(row["unrealized_pnl"] or 0)),
            realized_pnl=Decimal(str(row["realized_pnl"] or 0)),
            entry_time=row["entry_time"],
        )
