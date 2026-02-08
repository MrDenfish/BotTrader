"""SQLite storage adapter for dev/backtest use.

Lightweight storage that requires no external services.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from v2.core import registry
from v2.core.interfaces import StorageAdapter
from v2.core.types import Fill, Order, Position, Side

logger = logging.getLogger(__name__)


@registry.plugin("storage", "sqlite")
class SqliteStorage(StorageAdapter):
    """SQLite-backed storage for backtesting and development."""

    name = "sqlite"

    def __init__(
        self,
        event_bus=None,
        db_path: str = ":memory:",
        **kwargs: Any,
    ) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    async def connect(self) -> None:
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.info("SQLite connected: %s", self._db_path)

    async def disconnect(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("SQLite disconnected")

    async def record_fill(self, fill: Fill) -> None:
        self._conn.execute(
            """INSERT INTO fills
               (fill_id, order_id, symbol, side, price, qty, fee, fee_currency,
                is_maker, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fill.fill_id, fill.order_id, fill.symbol, fill.side.value,
                str(fill.price), str(fill.qty), str(fill.fee),
                fill.fee_currency, fill.is_maker, fill.timestamp.isoformat(),
            ),
        )
        self._conn.commit()

    async def record_order(self, order: Order) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO orders
               (order_id, symbol, side, order_type, price, qty, status, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order.order_id, order.symbol, order.side.value,
                order.order_type.value, str(order.price), str(order.qty),
                order.status.value, order.timestamp.isoformat(),
            ),
        )
        self._conn.commit()

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        if symbol:
            rows = self._conn.execute(
                "SELECT * FROM positions WHERE symbol = ?", (symbol,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM positions").fetchall()
        return [self._row_to_position(r) for r in rows]

    async def get_trades(
        self, symbol: str | None = None, since: datetime | None = None
    ) -> list[Fill]:
        query = "SELECT * FROM fills WHERE 1=1"
        params: list = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if since:
            query += " AND timestamp >= ?"
            params.append(since.isoformat())
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_fill(r) for r in rows]

    async def save_state(self, key: str, state: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)",
            (key, json.dumps(state, default=str)),
        )
        self._conn.commit()

    async def load_state(self, key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT value FROM state WHERE key = ?", (key,)
        ).fetchone()
        if row:
            return json.loads(row["value"])
        return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS fills (
                fill_id TEXT PRIMARY KEY,
                order_id TEXT,
                symbol TEXT,
                side TEXT,
                price TEXT,
                qty TEXT,
                fee TEXT,
                fee_currency TEXT,
                is_maker INTEGER,
                timestamp TEXT
            );
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                symbol TEXT,
                side TEXT,
                order_type TEXT,
                price TEXT,
                qty TEXT,
                status TEXT,
                timestamp TEXT
            );
            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                qty TEXT,
                avg_entry_price TEXT,
                cost_basis TEXT,
                unrealized_pnl TEXT,
                realized_pnl TEXT,
                entry_time TEXT
            );
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)

    @staticmethod
    def _row_to_fill(row: sqlite3.Row) -> Fill:
        return Fill(
            fill_id=row["fill_id"],
            order_id=row["order_id"],
            symbol=row["symbol"],
            side=Side(row["side"]),
            price=Decimal(row["price"]),
            qty=Decimal(row["qty"]),
            fee=Decimal(row["fee"]),
            fee_currency=row["fee_currency"],
            is_maker=bool(row["is_maker"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
        )

    @staticmethod
    def _row_to_position(row: sqlite3.Row) -> Position:
        return Position(
            symbol=row["symbol"],
            qty=Decimal(row["qty"]),
            avg_entry_price=Decimal(row["avg_entry_price"]),
            cost_basis=Decimal(row["cost_basis"]),
            unrealized_pnl=Decimal(row["unrealized_pnl"]),
            realized_pnl=Decimal(row["realized_pnl"]),
            entry_time=(
                datetime.fromisoformat(row["entry_time"])
                if row["entry_time"] else None
            ),
        )
