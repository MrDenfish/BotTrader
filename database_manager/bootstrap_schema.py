from sqlalchemy import text

async def ensure_trade_provenance_schema(async_engine) -> None:
    """
    Idempotent: safe to run on every startup.
    Adds ingest_via + last_reconciled_* columns and indexes if missing.
    """
    stmts = [
        "ALTER TABLE trade_records ADD COLUMN IF NOT EXISTS ingest_via text",
        "ALTER TABLE trade_records ADD COLUMN IF NOT EXISTS last_reconciled_at timestamptz",
        "ALTER TABLE trade_records ADD COLUMN IF NOT EXISTS last_reconciled_via text",
        "CREATE INDEX IF NOT EXISTS ix_trade_records_ingest_via ON trade_records (ingest_via)",
        "CREATE INDEX IF NOT EXISTS ix_trade_records_last_reconciled_at ON trade_records (last_reconciled_at)",
    ]
    async with async_engine.begin() as conn:
        for s in stmts:
            await conn.execute(text(s))


async def ensure_webhook_limit_only_positions_table(async_engine) -> None:
    """
    Idempotent: safe to run on every startup.
    Creates webhook_limit_only_positions table if it doesn't exist.

    This table stores limit-only positions from webhook orders that need
    monitoring by passive_order_manager.
    """
    create_table_stmt = """
    CREATE TABLE IF NOT EXISTS webhook_limit_only_positions (
        order_id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        entry_price DOUBLE PRECISION NOT NULL,
        size DOUBLE PRECISION NOT NULL,
        tp_price DOUBLE PRECISION NOT NULL,
        sl_price DOUBLE PRECISION NOT NULL,
        timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        source TEXT NOT NULL DEFAULT 'webhook_limit_only',
        order_data JSONB
    )
    """

    index_stmts = [
        "CREATE INDEX IF NOT EXISTS ix_webhook_limit_only_positions_symbol ON webhook_limit_only_positions (symbol)",
        "CREATE INDEX IF NOT EXISTS ix_webhook_limit_only_positions_timestamp ON webhook_limit_only_positions (timestamp)",
    ]

    async with async_engine.begin() as conn:
        await conn.execute(text(create_table_stmt))
        for s in index_stmts:
            await conn.execute(text(s))