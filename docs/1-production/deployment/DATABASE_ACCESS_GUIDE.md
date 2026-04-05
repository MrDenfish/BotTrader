# Database Access Guide - AWS Server

**Database**: PostgreSQL 16
**Container**: `db`
**Database Name**: `bot_trader_db`
**User**: `bot_user`
**Password**: (see `.env` file on AWS at `/opt/bot/.env`)

---

## Option 1: psql Interactive Terminal (Quick Queries)

### Connect via SSH

```bash
# SSH to server
ssh bottrader-aws

# Connect to psql
docker exec -it db psql -U bot_user -d bot_trader_db
```

### Once Connected:

```sql
-- List all tables
\dt

-- Describe v2_fills table
\d v2_fills

-- Recent fills (buys and sells)
SELECT
    timestamp,
    symbol,
    side,
    ROUND(price::numeric, 4) as price,
    ROUND(qty::numeric, 6) as qty,
    ROUND(fee::numeric, 4) as fee,
    metadata->>'signal_reason' as exit_reason
FROM v2_fills
WHERE timestamp >= NOW() - INTERVAL '24 hours'
ORDER BY timestamp DESC
LIMIT 20;

-- Exit psql
\q
```

### Useful psql Commands:

| Command | Description |
|---------|-------------|
| `\dt` | List all tables |
| `\d table_name` | Describe table structure |
| `\l` | List all databases |
| `\du` | List users |
| `\x` | Toggle expanded display (easier to read wide rows) |
| `\timing` | Show query execution time |
| `\q` | Quit psql |

---

## Option 2: Connect pgAdmin 4 from Desktop (SSH Tunnel)

### Step 1: Create SSH Tunnel

Open a terminal on your Mac and run:

```bash
# Forward local port 5433 to remote PostgreSQL port 5432
ssh -L 5433:localhost:5432 bottrader-aws -N
```

**Leave this terminal open** — it creates the tunnel.

### Step 2: Add Connection in pgAdmin 4

1. Open pgAdmin 4 on your desktop
2. Right-click "Servers" → "Register" → "Server"
3. **General Tab**:
   - Name: `BotTrader AWS (via SSH)`
4. **Connection Tab**:
   - Host: `localhost`
   - Port: `5433` (not 5432!)
   - Maintenance database: `bot_trader_db`
   - Username: `bot_user`
   - Password: (from `/opt/bot/.env` on AWS — `grep DB_PASSWORD .env`)
   - Save password: ✓

5. Click **Save**

Now you can browse the AWS database in pgAdmin just like your local one!

**To disconnect**: Close the SSH tunnel terminal (Ctrl+C)

---

## Option 3: One-off Queries via SSH

For quick queries without interactive session:

```bash
# Recent sells with exit reasons
ssh bottrader-aws 'docker exec db psql -U bot_user -d bot_trader_db -c "
SELECT
    timestamp,
    symbol,
    ROUND(price::numeric, 4) as price,
    metadata->>'\''signal_reason'\'' as exit_reason
FROM v2_fills
WHERE side = '\''sell'\''
  AND timestamp >= NOW() - INTERVAL '\''1 hour'\''
ORDER BY timestamp DESC
LIMIT 10;
"'
```

---

## v2 Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `v2_fills` | Every executed trade (buys and sells) |
| `v2_orders` | Submitted orders with status tracking |
| `v2_positions` | Current position state per symbol |
| `v2_state` | Key-value state persistence (guardrails, strategy state) |

### v2_fills Columns

| Column | Type | Notes |
|--------|------|-------|
| `fill_id` | TEXT (PK) | Unique fill identifier |
| `order_id` | TEXT | Parent order |
| `symbol` | TEXT | e.g., `BTC-USD` |
| `side` | TEXT | `buy` or `sell` |
| `price` | DOUBLE PRECISION | Fill price |
| `qty` | DOUBLE PRECISION | Fill quantity |
| `fee` | DOUBLE PRECISION | Fee amount |
| `fee_currency` | TEXT | Usually `USD` |
| `is_maker` | BOOLEAN | Maker or taker fill |
| `timestamp` | TIMESTAMPTZ | Fill time |
| `metadata` | JSONB | Signal reason, trigger, indicator snapshot |
| `exchange` | TEXT | e.g., `paper-kraken` |

**Key metadata fields** (access via `metadata->>'field_name'`):
- `signal_reason` — exit reason (e.g., `hard_stop`, `trailing_stop`, `stale_exit`)
- `trigger` — entry trigger (e.g., `score`, `score_high`)

### v2_positions Columns

| Column | Type | Notes |
|--------|------|-------|
| `symbol` | TEXT (PK) | Trading pair |
| `qty` | DOUBLE PRECISION | Current quantity held |
| `avg_entry_price` | DOUBLE PRECISION | Weighted average entry |
| `cost_basis` | DOUBLE PRECISION | Total cost |
| `unrealized_pnl` | DOUBLE PRECISION | May be stale — exit manager uses in-memory portfolio |
| `realized_pnl` | DOUBLE PRECISION | Cumulative realized P&L |
| `entry_time` | TIMESTAMPTZ | Position open time |

---

## Useful Queries for Monitoring

### 1. Exit Reason Breakdown

```sql
SELECT
    metadata->>'signal_reason' as exit_reason,
    COUNT(*) as count,
    ROUND(AVG(price * qty)::numeric, 2) as avg_notional,
    MIN(timestamp) as first_seen,
    MAX(timestamp) as last_seen
FROM v2_fills
WHERE side = 'sell'
  AND timestamp >= NOW() - INTERVAL '7 days'
GROUP BY metadata->>'signal_reason'
ORDER BY count DESC;
```

### 2. Recent Activity

```sql
SELECT
    timestamp,
    symbol,
    side,
    ROUND(price::numeric, 4) as price,
    ROUND(qty::numeric, 6) as qty,
    ROUND(fee::numeric, 4) as fee,
    metadata->>'signal_reason' as exit_reason,
    metadata->>'trigger' as trigger
FROM v2_fills
WHERE timestamp >= NOW() - INTERVAL '4 hours'
ORDER BY timestamp DESC;
```

### 3. Current Open Positions

```sql
SELECT
    symbol,
    ROUND(qty::numeric, 6) as qty,
    ROUND(avg_entry_price::numeric, 4) as avg_entry,
    ROUND(cost_basis::numeric, 2) as cost_basis,
    entry_time
FROM v2_positions
WHERE qty > 0.000001
ORDER BY entry_time DESC;
```

### 4. Daily Fill Summary

```sql
SELECT
    DATE(timestamp) as day,
    COUNT(*) FILTER (WHERE side = 'buy') as buys,
    COUNT(*) FILTER (WHERE side = 'sell') as sells,
    ROUND(SUM(price * qty) FILTER (WHERE side = 'buy')::numeric, 2) as buy_volume,
    ROUND(SUM(price * qty) FILTER (WHERE side = 'sell')::numeric, 2) as sell_volume,
    ROUND(SUM(fee)::numeric, 2) as total_fees
FROM v2_fills
WHERE timestamp >= NOW() - INTERVAL '14 days'
GROUP BY DATE(timestamp)
ORDER BY day DESC;
```

### 5. Exit Reason Effectiveness (Matched Trades)

```sql
-- For each sell, find the most recent buy of the same symbol to estimate P&L
WITH sells AS (
    SELECT
        fill_id,
        symbol,
        price as sell_price,
        qty as sell_qty,
        timestamp as sell_time,
        metadata->>'signal_reason' as exit_reason
    FROM v2_fills
    WHERE side = 'sell'
      AND timestamp >= NOW() - INTERVAL '14 days'
),
buys AS (
    SELECT DISTINCT ON (symbol, fill_id)
        symbol,
        price as buy_price,
        timestamp as buy_time
    FROM v2_fills
    WHERE side = 'buy'
)
SELECT
    s.exit_reason,
    COUNT(*) as total,
    ROUND(AVG((s.sell_price - b.buy_price) / b.buy_price * 100)::numeric, 2) as avg_pnl_pct
FROM sells s
LEFT JOIN LATERAL (
    SELECT buy_price, buy_time
    FROM buys b
    WHERE b.symbol = s.symbol AND b.buy_time < s.sell_time
    ORDER BY b.buy_time DESC LIMIT 1
) b ON TRUE
WHERE s.exit_reason IS NOT NULL
GROUP BY s.exit_reason
ORDER BY total DESC;
```

### 6. Check Strategy State

```sql
SELECT
    key,
    updated_at,
    jsonb_pretty(value) as state
FROM v2_state
ORDER BY updated_at DESC;
```

---

## Troubleshooting

### SSH Tunnel Won't Connect

```bash
# Check if port 5433 is already in use
lsof -i :5433

# Kill existing tunnel if needed
pkill -f "ssh.*5433:localhost:5432"

# Try tunnel again
ssh -L 5433:localhost:5432 bottrader-aws -N
```

### pgAdmin Can't Connect

1. Make sure SSH tunnel is running (check terminal)
2. Verify connection settings:
   - Host: `localhost` (not the AWS IP!)
   - Port: `5433` (not 5432!)
3. Test tunnel manually:

```bash
psql -h localhost -p 5433 -U bot_user -d bot_trader_db
```

### Password Doesn't Work

Check `.env` file for current password:

```bash
ssh bottrader-aws "grep DB_PASSWORD /opt/bot/.env"
```

---

**Last Updated:** 2026-04-03
