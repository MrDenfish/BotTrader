-- Cash Transactions Table
-- Tracks all USD deposits and withdrawals from Coinbase accounts
-- Used for accurate cash balance and equity curve calculations

CREATE TABLE IF NOT EXISTS public.cash_transactions (
    id SERIAL PRIMARY KEY,
    transaction_id VARCHAR(50) UNIQUE,  -- Coinbase transaction ID
    transaction_date TIMESTAMPTZ NOT NULL,
    transaction_type VARCHAR(30) NOT NULL,  -- From CSV: 'Deposit', 'Pro Deposit', 'Pro Withdrawal', etc.
    normalized_type VARCHAR(20),  -- Normalized: 'deposit' or 'withdrawal'
    asset VARCHAR(10) NOT NULL,  -- Should always be 'USD' for cash transactions
    quantity NUMERIC(20,8) NOT NULL,  -- Can be negative for withdrawals in CSV
    amount_usd NUMERIC(20,8) NOT NULL,  -- Absolute value, always positive
    subtotal NUMERIC(20,8),
    total NUMERIC(20,8),
    fees NUMERIC(20,8) DEFAULT 0,
    notes TEXT,
    source VARCHAR(50),  -- 'coinbase', 'coinbase_pro', 'coinbase_advanced'
    imported_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT valid_asset CHECK (asset = 'USD'),
    CONSTRAINT valid_amount CHECK (amount_usd >= 0)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_cash_tx_date ON public.cash_transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_cash_tx_type ON public.cash_transactions(normalized_type);
CREATE INDEX IF NOT EXISTS idx_cash_tx_id ON public.cash_transactions(transaction_id);

-- Comments for documentation
COMMENT ON TABLE public.cash_transactions IS 'USD deposits and withdrawals from Coinbase accounts since inception (2023-11-23)';
COMMENT ON COLUMN public.cash_transactions.transaction_id IS 'Unique Coinbase transaction ID from CSV';
COMMENT ON COLUMN public.cash_transactions.normalized_type IS 'Simplified type: deposit (money in) or withdrawal (money out)';
COMMENT ON COLUMN public.cash_transactions.amount_usd IS 'Absolute value of transaction, always positive';
COMMENT ON COLUMN public.cash_transactions.quantity IS 'Raw quantity from CSV, can be negative';
