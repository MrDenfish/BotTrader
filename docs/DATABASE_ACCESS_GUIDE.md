# Database Access Guide - AWS Server

**Database**: PostgreSQL 16
**Container**: `db`
**Database Name**: `bot_trader_db`
**User**: `bot_user`
**Password**: (in .env file)

---

## Option 1: psql Interactive Terminal (Quick Queries)

### Connect via SSH (Manual)

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

-- Describe trade_records table
\d trade_records

-- Query recent sells with exit reasons
SELECT
    order_time,
    symbol,
    side,
    pnl_usd,
    exit_reason,
    trigger->>'trigger' as trigger_type
FROM trade_records
WHERE side = 'sell'
  AND order_time >= NOW() - INTERVAL '24 hours'
ORDER BY order_time DESC
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

**Best option if you want to use pgAdmin like you do locally!**

### Step 1: Create SSH Tunnel

Open a terminal on your Mac and run:

```bash
# Forward local port 5433 to remote PostgreSQL port 5432
ssh -L 5433:localhost:5432 bottrader-aws -N
```

**Leave this terminal open** - it creates the tunnel.

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
   - Password: `7317botTrade4ssm` (from .env)
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
    order_time,
    symbol,
    pnl_usd,
    exit_reason
FROM trade_records
WHERE side = '\''sell'\''
  AND order_time >= NOW() - INTERVAL '\''1 hour'\''
ORDER BY order_time DESC
LIMIT 10;
"'
```

---

## Option 4: Web-based Adminer (Lightweight Alternative)

If you want a web UI without SSH tunnel, you can deploy Adminer:

### Add to docker-compose.yml:

```yaml
adminer:
  image: adminer:latest
  restart: always
  ports:
    - "8080:8080"
  environment:
    ADMINER_DEFAULT_SERVER: db
```

### Deploy:

```bash
ssh bottrader-aws
cd /opt/bot
docker compose up -d adminer
```

### Access:

Open browser: `http://your-aws-ip:8080`

**Login**:
- System: PostgreSQL
- Server: db
- Username: bot_user
- Password: 7317botTrade4ssm
- Database: bot_trader_db

**Security Note**: Only enable this temporarily or restrict to your IP!

---

## Recommended Setup for You

Since you already use pgAdmin 4, I recommend **Option 2 (SSH Tunnel)**:

1. Create a saved alias in `~/.ssh/config`:

```bash
# Edit ~/.ssh/config
Host bottrader-db-tunnel
    HostName your-aws-ip
    User ubuntu
    IdentityFile ~/.ssh/your-key.pem
    LocalForward 5433 localhost:5432
```

2. Then connecting is just:

```bash
ssh bottrader-db-tunnel -N
```

3. Add server in pgAdmin once, and you can reconnect anytime by running the SSH tunnel!

---

## Useful Queries for Monitoring

### 1. Check exit_reason Population

```sql
SELECT
    exit_reason,
    COUNT(*) as count,
    ROUND(AVG(pnl_usd), 2) as avg_pnl,
    MIN(order_time) as first_seen,
    MAX(order_time) as last_seen
FROM trade_records
WHERE side = 'sell'
  AND order_time >= '2025-12-01'  -- After deployment
GROUP BY exit_reason
ORDER BY count DESC;
```

### 2. Recent Activity

```sql
SELECT
    order_time,
    symbol,
    side,
    ROUND(CAST(price AS NUMERIC), 4) as price,
    ROUND(CAST(size AS NUMERIC), 6) as size,
    ROUND(CAST(pnl_usd AS NUMERIC), 2) as pnl,
    exit_reason,
    trigger->>'trigger' as trigger_type
FROM trade_records
WHERE order_time >= NOW() - INTERVAL '1 hour'
ORDER BY order_time DESC;
```

### 3. Position Monitor Performance

```sql
-- Verify soft stops are working
SELECT
    symbol,
    order_time,
    ROUND(CAST(pnl_usd AS NUMERIC), 2) as pnl,
    exit_reason,
    CASE
        WHEN pnl_usd < -1.0 THEN '⚠️ Large loss'
        WHEN pnl_usd BETWEEN -1.0 AND -0.5 THEN '✅ Normal stop'
        ELSE 'Other'
    END as assessment
FROM trade_records
WHERE exit_reason = 'SOFT_STOP'
  AND order_time >= '2025-12-01'
ORDER BY pnl_usd ASC
LIMIT 20;
```

### 4. Exit Reason Effectiveness

```sql
WITH exit_stats AS (
    SELECT
        exit_reason,
        COUNT(*) as total,
        SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
        AVG(CASE WHEN pnl_usd > 0 THEN pnl_usd END) as avg_win,
        AVG(CASE WHEN pnl_usd <= 0 THEN pnl_usd END) as avg_loss,
        SUM(pnl_usd) as total_pnl
    FROM trade_records
    WHERE side = 'sell'
      AND order_time >= '2025-12-01'
      AND exit_reason IS NOT NULL
    GROUP BY exit_reason
)
SELECT
    exit_reason,
    total,
    wins,
    ROUND(100.0 * wins / total, 1) as win_rate_pct,
    ROUND(avg_win::numeric, 2) as avg_win,
    ROUND(avg_loss::numeric, 2) as avg_loss,
    ROUND(total_pnl::numeric, 2) as total_pnl,
    ROUND((avg_win / NULLIF(ABS(avg_loss), 0))::numeric, 2) as risk_reward_ratio
FROM exit_stats
ORDER BY total DESC;
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
grep DB_PASSWORD .env
```

---

**My Recommendation**: Use Option 2 (SSH Tunnel + pgAdmin) for the best experience!
