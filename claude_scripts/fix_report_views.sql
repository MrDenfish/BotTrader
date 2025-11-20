-- ================================================
-- CLANKER Position Bug Fix - Part 2: View Update
-- ================================================
-- This updates the views to handle both correct and
-- incorrect remaining_size values (defensive fix)
-- ================================================

-- Drop dependent views first
DROP VIEW IF EXISTS public.report_positions CASCADE;
DROP VIEW IF EXISTS public.report_trades CASCADE;

-- Create improved report_trades view
CREATE OR REPLACE VIEW public.report_trades AS
SELECT 
    symbol,
    LOWER(side::text) AS side,
    order_time AS ts,
    price::numeric AS price,
    
    -- FIX: If status is 'filled', use size, otherwise subtract remaining_size
    (CASE 
        WHEN LOWER(side::text) LIKE 'sell%' THEN -1
        ELSE 1
    END)::numeric * 
    (CASE
        WHEN status = 'filled' OR remaining_size IS NULL THEN size
        ELSE (size - COALESCE(remaining_size, 0))
    END)::numeric AS qty_signed,
    
    (ABS(
        CASE
            WHEN status = 'filled' OR remaining_size IS NULL THEN size
            ELSE (size - COALESCE(remaining_size, 0))
        END
    )::numeric * price::numeric) AS notional_usd,
    
    COALESCE(total_fees_usd, 0)::numeric AS fee_usd,
    COALESCE(realized_profit, COALESCE(pnl_usd, 0))::numeric AS realized_pnl
FROM trade_records
WHERE (
    (status = 'filled' AND size > 0) OR
    (COALESCE(size, 0) - COALESCE(remaining_size, 0)) <> 0
)
ORDER BY order_time;

-- Recreate report_positions view
CREATE OR REPLACE VIEW public.report_positions AS
SELECT 
    symbol,
    SUM(qty_signed) AS position_qty,
    CASE
        WHEN SUM(qty_signed) > 0 THEN 
            SUM(CASE WHEN qty_signed > 0 THEN qty_signed * price ELSE NULL END) / 
            NULLIF(SUM(GREATEST(qty_signed, 0)), 0)
        WHEN SUM(qty_signed) < 0 THEN 
            SUM(CASE WHEN qty_signed < 0 THEN (-qty_signed) * price ELSE NULL END) / 
            NULLIF(SUM(GREATEST(-qty_signed, 0)), 0)
        ELSE NULL
    END AS avg_entry_price,
    CASE
        WHEN SUM(qty_signed) > 0 THEN 
            SUM(CASE WHEN qty_signed > 0 THEN qty_signed * price ELSE NULL END) / 
            NULLIF(SUM(GREATEST(qty_signed, 0)), 0)
        WHEN SUM(qty_signed) < 0 THEN 
            SUM(CASE WHEN qty_signed < 0 THEN (-qty_signed) * price ELSE NULL END) / 
            NULLIF(SUM(GREATEST(-qty_signed, 0)), 0)
        ELSE NULL
    END AS price
FROM report_trades
GROUP BY symbol
HAVING SUM(qty_signed) <> 0
ORDER BY ABS(SUM(qty_signed)) DESC;

-- Verify the views were created successfully
SELECT 'Views created successfully!' as status;

-- Quick verification query
SELECT 
    'VERIFICATION' as info,
    symbol,
    position_qty,
    avg_entry_price,
    (position_qty * avg_entry_price) as notional_usd
FROM report_positions
WHERE symbol = 'CLANKER-USD'
UNION ALL
SELECT 
    'TOTAL NOTIONAL' as info,
    NULL as symbol,
    NULL as position_qty,
    NULL as avg_entry_price,
    SUM(ABS(position_qty * avg_entry_price)) as notional_usd
FROM report_positions;
