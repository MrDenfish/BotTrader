"""
FIFO Helpers - Utilities for Querying FIFO Allocations

This module provides helper functions to query P&L from the fifo_allocations table,
replacing deprecated realized_profit and pnl_usd columns in trade_records.

Usage:
    from botreport.fifo_helpers import get_fifo_pnl_subquery, get_fifo_pnl_join

Background:
    The trade_records table previously had pnl_usd and realized_profit columns
    that were populated by inline FIFO computation. This caused data corruption
    due to dual FIFO system conflicts. The FIFO engine (fifo_allocations table)
    is now the sole source of truth for P&L calculations.
"""

import os
from typing import Optional

# FIFO allocation version to use (can be overridden via environment)
DEFAULT_FIFO_VERSION = int(os.getenv("FIFO_ALLOCATION_VERSION", "2"))


def get_fifo_pnl_subquery(
    version: int = DEFAULT_FIFO_VERSION,
    where_clause: str = "",
    alias: str = "fifo_pnl"
) -> str:
    """
    Generate SQL subquery to get P&L from FIFO allocations for a single trade.

    This can be used in SELECT or WHERE clauses to replace:
        COALESCE(realized_profit, pnl_usd)

    Args:
        version: FIFO allocation version (default: 2)
        where_clause: Additional WHERE conditions (e.g., "AND fa.created_at >= :start_time")
        alias: Column alias for the result

    Returns:
        SQL subquery string that can be embedded in larger queries

    Example:
        SELECT
            order_id,
            symbol,
            {get_fifo_pnl_subquery()} as pnl
        FROM trade_records
        WHERE side = 'sell'
    """
    where = f"AND {where_clause}" if where_clause else ""

    return f"""(
        SELECT COALESCE(SUM(fa.pnl_usd), 0)
        FROM fifo_allocations fa
        WHERE fa.sell_order_id = trade_records.order_id
          AND fa.allocation_version = {version}
          {where}
    ) AS {alias}"""


def get_fifo_pnl_join(
    version: int = DEFAULT_FIFO_VERSION,
    table_alias: str = "fa"
) -> str:
    """
    Generate SQL JOIN clause to link trade_records with fifo_allocations.

    This allows aggregating P&L per trade without subqueries.

    Args:
        version: FIFO allocation version (default: 2)
        table_alias: Alias for fifo_allocations table

    Returns:
        SQL JOIN clause

    Example:
        SELECT
            tr.order_id,
            tr.symbol,
            COALESCE(SUM(fa.pnl_usd), 0) as total_pnl
        FROM trade_records tr
        {get_fifo_pnl_join()}
        WHERE tr.side = 'sell'
        GROUP BY tr.order_id, tr.symbol
    """
    return f"""LEFT JOIN fifo_allocations {table_alias}
        ON {table_alias}.sell_order_id = trade_records.order_id
        AND {table_alias}.allocation_version = {version}"""


def get_fifo_pnl_cte(
    version: int = DEFAULT_FIFO_VERSION,
    time_filter: Optional[str] = None
) -> str:
    """
    Generate SQL CTE (Common Table Expression) for FIFO P&L per trade.

    This creates a reusable view that can be joined multiple times in complex queries.

    Args:
        version: FIFO allocation version (default: 2)
        time_filter: Optional WHERE clause for trade_records filtering
                     (e.g., "order_time >= NOW() - INTERVAL '24 hours'")

    Returns:
        SQL CTE string

    Example:
        WITH {get_fifo_pnl_cte(time_filter="order_time >= :start")}
        SELECT
            fp.symbol,
            COUNT(*) as trades,
            SUM(fp.pnl) as total_pnl
        FROM fifo_pnl fp
        GROUP BY fp.symbol
    """
    where = f"WHERE {time_filter}" if time_filter else ""

    return f"""fifo_pnl AS (
        SELECT
            tr.order_id,
            tr.symbol,
            tr.order_time,
            tr.side,
            COALESCE(SUM(fa.pnl_usd), 0) as pnl,
            COUNT(fa.allocation_id) as allocation_count
        FROM trade_records tr
        LEFT JOIN fifo_allocations fa
            ON fa.sell_order_id = tr.order_id
            AND fa.allocation_version = {version}
        {where}
        GROUP BY tr.order_id, tr.symbol, tr.order_time, tr.side
    )"""


def get_fifo_stats_query(
    symbol: Optional[str] = None,
    hours_back: int = 24,
    version: int = DEFAULT_FIFO_VERSION
) -> str:
    """
    Generate complete query for FIFO-based trade statistics.

    Args:
        symbol: Optional symbol filter (e.g., "BTC-USD")
        hours_back: Lookback window in hours
        version: FIFO allocation version

    Returns:
        Complete SQL query string

    Example:
        query = get_fifo_stats_query(symbol="BTC-USD", hours_back=24)
        result = conn.execute(text(query))
    """
    symbol_filter = f"AND tr.symbol = '{symbol}'" if symbol else ""

    return f"""
    WITH {get_fifo_pnl_cte(
        version=version,
        time_filter=f"order_time >= NOW() - INTERVAL '{hours_back} hours' AND side = 'sell' {symbol_filter}"
    )}
    SELECT
        symbol,
        COUNT(*) as total_trades,
        COUNT(*) FILTER (WHERE pnl > 0) as wins,
        COUNT(*) FILTER (WHERE pnl < 0) as losses,
        COUNT(*) FILTER (WHERE pnl = 0) as breakeven,
        SUM(pnl) as total_pnl,
        AVG(pnl) FILTER (WHERE pnl > 0) as avg_win,
        AVG(pnl) FILTER (WHERE pnl < 0) as avg_loss,
        SUM(pnl) FILTER (WHERE pnl > 0) as gross_profit,
        ABS(SUM(pnl) FILTER (WHERE pnl < 0)) as gross_loss
    FROM fifo_pnl
    GROUP BY symbol
    ORDER BY total_pnl DESC
    """


# Migration helper for gradual rollout
def use_legacy_pnl() -> bool:
    """
    Check if we should use legacy pnl_usd/realized_profit columns.

    This allows for gradual migration by setting FIFO_USE_LEGACY=1
    in environment variables.

    Returns:
        True if should use legacy columns, False if should use FIFO
    """
    return os.getenv("FIFO_USE_LEGACY", "0") == "1"


def get_pnl_column_expression(legacy_fallback: bool = False) -> str:
    """
    Get the appropriate P&L column expression based on configuration.

    Args:
        legacy_fallback: If True, include COALESCE with legacy columns as fallback

    Returns:
        SQL expression for P&L

    Example:
        # Without fallback (FIFO only)
        SELECT {get_pnl_column_expression()} as pnl FROM ...

        # With fallback (tries FIFO, falls back to legacy)
        SELECT {get_pnl_column_expression(legacy_fallback=True)} as pnl FROM ...
    """
    if use_legacy_pnl():
        # Legacy mode: use old columns
        return "COALESCE(realized_profit, pnl_usd, 0)"

    # FIFO mode
    fifo_expr = get_fifo_pnl_subquery(alias="")

    if legacy_fallback:
        # Include fallback to legacy columns (for transition period)
        return f"COALESCE({fifo_expr}, realized_profit, pnl_usd, 0)"
    else:
        # FIFO only (recommended)
        return f"COALESCE({fifo_expr}, 0)"
