# PostgreSQL Connection Troubleshooting - AWS Server

## Problem: "Connection refused" on localhost:5432

This typically means PostgreSQL is either:
1. Not running
2. Running but not listening on TCP/IP
3. Running via Unix socket only

---

## Quick Fix: Use Unix Socket Instead

Instead of `-h localhost`, use the Unix socket:

```bash
# Option 1: Connect via Unix socket (no -h flag)
psql -U bot_user -d bot_trader_db -f database/migrations/001_create_fifo_allocations_tables.sql

# Option 2: Specify socket directory explicitly
psql -h /var/run/postgresql -U bot_user -d bot_trader_db -f database/migrations/001_create_fifo_allocations_tables.sql
```

**This should work immediately without any PostgreSQL configuration changes.**

---

## Verify PostgreSQL Status

```bash
# Check if PostgreSQL is running
sudo systemctl status postgresql

# If not running, start it
sudo systemctl start postgresql

# Enable on boot
sudo systemctl enable postgresql
```

---

## Check PostgreSQL Version and Socket Location

```bash
# Find PostgreSQL version
psql --version

# Find socket location
sudo ls -la /var/run/postgresql/

# Expected output:
# srwxrwxrwx 1 postgres postgres ... .s.PGSQL.5432
```

---

## Test Different Connection Methods

```bash
# Method 1: Unix socket (no host)
psql -U bot_user -d bot_trader_db -c "SELECT 1;"

# Method 2: Unix socket (explicit)
psql -h /var/run/postgresql -U bot_user -d bot_trader_db -c "SELECT 1;"

# Method 3: TCP/IP on localhost
psql -h localhost -U bot_user -d bot_trader_db -c "SELECT 1;"

# Method 4: TCP/IP on 127.0.0.1
psql -h 127.0.0.1 -U bot_user -d bot_trader_db -c "SELECT 1;"
```

One of these should work. Use whichever succeeds for the migration.

---

## If You Need TCP/IP Connections (Optional)

Only needed if Unix socket doesn't work or if you specifically need TCP/IP.

### 1. Enable TCP/IP Listening

```bash
# Find PostgreSQL version
psql --version
# Example: psql (PostgreSQL) 14.10

# Edit postgresql.conf (adjust version number)
sudo nano /etc/postgresql/14/main/postgresql.conf

# Find and uncomment/change this line:
listen_addresses = 'localhost'  # or '*' for all interfaces
```

### 2. Allow Local Connections

```bash
# Edit pg_hba.conf
sudo nano /etc/postgresql/14/main/pg_hba.conf

# Add or ensure these lines exist:
# TYPE  DATABASE        USER            ADDRESS                 METHOD
local   all             all                                     peer
host    all             all             127.0.0.1/32            md5
host    all             all             ::1/128                 md5
```

### 3. Restart PostgreSQL

```bash
sudo systemctl restart postgresql

# Verify it's listening on TCP
sudo netstat -plnt | grep 5432
# Expected: tcp  0  0  127.0.0.1:5432  0.0.0.0:*  LISTEN
```

---

## Updated Migration Commands

**Use Unix socket (recommended):**

```bash
cd /opt/bot

# Run migration via Unix socket
psql -U bot_user -d bot_trader_db -f database/migrations/001_create_fifo_allocations_tables.sql

# All other commands
psql -U bot_user -d bot_trader_db -c "\dt" | grep fifo
psql -U bot_user -d bot_trader_db -c "\dv" | grep "v_"
psql -U bot_user -d bot_trader_db -c "SELECT * FROM v_allocation_health;"
```

---

## Check Application Connection String

Your application might already be using Unix sockets. Check:

```bash
cd /opt/bot
cat .env | grep DATABASE_URL
```

**Common patterns:**

```bash
# Unix socket (no host/port in URL)
DATABASE_URL=postgresql://bot_user:password@/bot_trader_db

# TCP/IP
DATABASE_URL=postgresql://bot_user:password@localhost:5432/bot_trader_db
DATABASE_URL=postgresql://bot_user:password@127.0.0.1:5432/bot_trader_db
```

If your app uses Unix socket, the migration should too!

---

## Updated AWS Deployment Checklist

Replace all `psql -h localhost` commands with `psql` (no -h flag):

```bash
# WRONG (if TCP not enabled)
psql -h localhost -U bot_user -d bot_trader_db -f database/migrations/001_create_fifo_allocations_tables.sql

# CORRECT (Unix socket)
psql -U bot_user -d bot_trader_db -f database/migrations/001_create_fifo_allocations_tables.sql
```

---

## Quick Connection Test

```bash
# Test connection
psql -U bot_user -d bot_trader_db -c "SELECT current_database(), current_user, version();"

# If this works, proceed with migration
psql -U bot_user -d bot_trader_db -f database/migrations/001_create_fifo_allocations_tables.sql
```

---

## Summary

**Recommended approach:**
1. Use Unix socket connections (omit `-h localhost`)
2. This is actually more secure and faster than TCP
3. No PostgreSQL configuration changes needed

**Quick fix for all commands in deployment guide:**
- Remove `-h localhost` from all `psql` commands
- Keep everything else the same

Example:
```bash
# Old command
psql -h localhost -U bot_user -d bot_trader_db -c "SELECT 1;"

# New command
psql -U bot_user -d bot_trader_db -c "SELECT 1;"
```

---

**Most likely solution:** Just remove `-h localhost` and the migration will work!
