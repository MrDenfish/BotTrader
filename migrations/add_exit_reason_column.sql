-- Migration: Add exit_reason column to trade_records
-- Date: 2025-12-01
-- Purpose: Track which exit mechanism triggered each sell order
--          (SOFT_STOP, HARD_STOP, SIGNAL_EXIT, TRAILING_STOP, TAKE_PROFIT, MANUAL)

-- Add the new column
ALTER TABLE trade_records
ADD COLUMN IF NOT EXISTS exit_reason VARCHAR(50);

-- Create index for efficient querying by exit_reason
CREATE INDEX IF NOT EXISTS idx_trade_records_exit_reason
ON trade_records(exit_reason)
WHERE exit_reason IS NOT NULL;

-- Add comment for documentation
COMMENT ON COLUMN trade_records.exit_reason IS
'Exit mechanism that triggered this sell order. Values: SOFT_STOP, HARD_STOP, SIGNAL_EXIT, TRAILING_STOP, TAKE_PROFIT, MANUAL, or NULL for buy orders';

-- Verify migration
SELECT
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_name = 'trade_records'
  AND column_name = 'exit_reason';

-- Show sample of recent trades that would benefit from this field
SELECT
    order_id,
    symbol,
    side,
    order_time,
    price,
    pnl_usd,
    trigger,
    exit_reason
FROM trade_records
WHERE side = 'sell'
  AND order_time >= NOW() - INTERVAL '7 days'
ORDER BY order_time DESC
LIMIT 10;
