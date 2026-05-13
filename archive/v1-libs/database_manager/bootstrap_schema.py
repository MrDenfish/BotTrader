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