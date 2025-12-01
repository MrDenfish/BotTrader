-- Check trade_records schema and sample data
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_name = 'trade_records'
ORDER BY ordinal_position;

-- Sample existing trade
SELECT *
FROM trade_records
WHERE side = 'buy'
LIMIT 1;
