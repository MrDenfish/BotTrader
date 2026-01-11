# Data Collection & Docker Container Architecture

**Created**: January 11, 2026
**Last Updated**: January 11, 2026
**Status**: Active Documentation

---

## Executive Summary

The optimization data collection infrastructure is **completely independent** of the Docker container lifecycle. This document explains the architectural separation between data collection and application containers, ensuring developers understand what persists across rebuilds and what doesn't.

**Key Takeaway**: You can rebuild Docker containers at any time without affecting data collection. All data collection components run on the host system and persist across container rebuilds.

---

## Architecture Overview

### System Components

```
┌─────────────────────────────────────────────────────────────┐
│                        AWS Host System                       │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  Cron Jobs (Host)                                            │
│  ├─ Weekly Strategy Review (Mondays 9am PT)                  │
│  └─ Daily Email Reports (every 6 hours)                      │
│                                                               │
│  Data Collection Scripts (Host Filesystem)                   │
│  ├─ /opt/bot/weekly_strategy_review.sh                       │
│  ├─ /opt/bot/queries/weekly_symbol_performance.sql           │
│  ├─ /opt/bot/queries/weekly_signal_quality.sql               │
│  └─ /opt/bot/queries/weekly_timing_analysis.sql              │
│                                                               │
│  Logs & Reports (Host Filesystem)                            │
│  └─ /opt/bot/logs/weekly_review_YYYY-MM-DD.txt               │
│                                                               │
├─────────────────────────────────────────────────────────────┤
│                     Docker Containers                         │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐             │
│  │ webhook    │  │ sighook    │  │ report-job │             │
│  │ (Python)   │  │ (Python)   │  │ (Python)   │             │
│  └────────────┘  └────────────┘  └────────────┘             │
│         │                │               │                    │
│         └────────────────┴───────────────┘                    │
│                          │                                    │
│                  ┌───────▼────────┐                           │
│                  │   db (Postgres) │                          │
│                  │  External Volume │                         │
│                  └─────────────────┘                          │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

---

## Host vs Container Components

### Host-Based (Persistent Across Rebuilds)

**Location**: AWS host system (`/opt/bot/`)

| Component | Path | Purpose | Survives Rebuild? |
|-----------|------|---------|-------------------|
| **Weekly Script** | `/opt/bot/weekly_strategy_review.sh` | Generates weekly analysis reports | ✅ YES |
| **SQL Queries** | `/opt/bot/queries/*.sql` | Analysis query definitions | ✅ YES |
| **Cron Jobs** | Host `crontab` | Scheduled execution | ✅ YES |
| **Reports** | `/opt/bot/logs/weekly_review_*.txt` | Historical reports | ✅ YES |
| **Logs Directory** | `/opt/bot/logs/` | Application logs, reports | ✅ YES |
| **Environment** | `/opt/bot/.env` | Configuration | ✅ YES |

**Key Characteristic**: These files exist on the host filesystem and are **never touched** by Docker builds.

---

### Container-Based (Rebuilt on Code Changes)

**Location**: Inside Docker containers (ephemeral)

| Component | Container | Purpose | Survives Rebuild? |
|-----------|-----------|---------|-------------------|
| **Trading Logic** | `sighook` | Strategy execution | ❌ NO (rebuilt) |
| **Webhook Server** | `webhook` | Order management | ❌ NO (rebuilt) |
| **Report Generator** | `report-job` | Daily email reports | ❌ NO (rebuilt) |
| **Python Dependencies** | All containers | Libraries, packages | ❌ NO (rebuilt) |
| **Application Code** | All containers | Python modules | ❌ NO (rebuilt) |

**Key Characteristic**: These are rebuilt from source code every time you run `docker compose build`.

---

### Database Layer (External Volume)

**Location**: Docker volume (`bottrader-aws_pg_data`)

| Component | Storage | Purpose | Survives Rebuild? |
|-----------|---------|---------|-------------------|
| **Trade Records** | External volume | All historical trades | ✅ YES |
| **Strategy Snapshots** | External volume | Configuration versions | ✅ YES |
| **Trade Linkage** | External volume | Trade-to-strategy links | ✅ YES |
| **FIFO Allocations** | External volume | P&L calculations | ✅ YES |
| **Cash Transactions** | External volume | Deposit/withdrawal history | ✅ YES |
| **Market Conditions** | External volume | Market regime tags | ✅ YES |

**Key Characteristic**: External volume (`pg_data`) persists independently of container lifecycle.

---

## How Data Collection Works

### Weekly Report Generation Flow

```
┌─────────────────────────────────────────────────────────────┐
│ Step 1: Cron Triggers (Host)                                 │
│   • Cron daemon on AWS host                                  │
│   • Schedule: 0 9 * * 1 (Every Monday 9am PT)                │
│   • Command: /opt/bot/weekly_strategy_review.sh              │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│ Step 2: Script Executes (Host)                               │
│   • Bash script runs on host system                          │
│   • Reads SQL query files from /opt/bot/queries/             │
│   • Creates output file: /opt/bot/logs/weekly_review_*.txt   │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│ Step 3: Database Queries (Docker Exec)                       │
│   • Script uses: docker exec db psql ...                     │
│   • Queries database from OUTSIDE container                  │
│   • Database container must be running                       │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│ Step 4: Report Generation (Host)                             │
│   • SQL results written to output file                       │
│   • File saved on host: /opt/bot/logs/                       │
│   • Accessible outside Docker ecosystem                      │
└─────────────────────────────────────────────────────────────┘
```

**Critical Point**: The entire process runs **outside Docker containers**. The script only reaches into the `db` container to query data.

---

## Docker Build Impact Analysis

### When You Run: `docker compose build --no-cache`

**What Gets Rebuilt**:
```bash
✅ webhook container
   └─ Dockerfile.bot with latest Python code
   └─ Dependencies from requirements.txt
   └─ webhook/webhook_order_manager.py
   └─ webhook/listener.py

✅ sighook container
   └─ Dockerfile.bot with latest Python code
   └─ Dependencies from requirements.txt
   └─ sighook/trading_strategy.py
   └─ sighook/signal_processor.py

✅ report-job container
   └─ Dockerfile.report with latest Python code
   └─ Dependencies from requirements.txt
   └─ botreport/aws_daily_report.py

✅ leaderboard-job container
   └─ Dockerfile.report with latest Python code
   └─ SharedDataManager/leaderboard_runner.py
```

**What DOES NOT Get Rebuilt**:
```bash
❌ /opt/bot/weekly_strategy_review.sh (host file)
❌ /opt/bot/queries/*.sql (host files)
❌ /opt/bot/logs/* (host files)
❌ /opt/bot/.env (host file)
❌ Cron jobs (host system)
❌ Database data (external volume)
❌ pg_data volume (external)
```

**What Persists Across Rebuilds**:
```bash
✅ All historical trade data
✅ Strategy snapshots and linkage
✅ Previous weekly reports
✅ SQL query definitions
✅ Cron job schedule
✅ Database connections (reconnect after rebuild)
```

---

## Scenarios & Impact

### Scenario 1: Code Update in sighook

**Action**:
```bash
# Make changes to sighook/trading_strategy.py
ssh bottrader-aws "cd /opt/bot && git pull"
docker compose --env-file /opt/bot/.env -f docker-compose.aws.yml build sighook --no-cache
docker compose -f docker-compose.aws.yml up -d sighook
```

**Impact on Data Collection**:
- ✅ **No impact** - Weekly reports continue
- ✅ **No impact** - SQL queries unchanged
- ✅ **No impact** - Cron jobs continue
- ✅ **No impact** - Historical data intact
- ⚠️ **Minor** - New trades use updated strategy code

**Data Collection Status**: ✅ **Fully Operational**

---

### Scenario 2: Full Container Rebuild

**Action**:
```bash
docker compose --env-file /opt/bot/.env -f docker-compose.aws.yml build --no-cache
docker compose -f docker-compose.aws.yml up -d
```

**Impact on Data Collection**:
- ✅ **No impact** - Weekly script on host, untouched
- ✅ **No impact** - SQL queries on host, untouched
- ✅ **No impact** - Cron jobs on host, continue
- ✅ **No impact** - Database data in external volume
- ⚠️ **Temporary** - Database connection briefly interrupted (containers restart)
- ⚠️ **Minor** - If rebuild at Monday 9am, one report might fail

**Data Collection Status**: ✅ **Fully Operational** (after containers restart)

**Recovery Time**: ~2 minutes (container restart time)

---

### Scenario 3: Rebuild on Monday 9am (Worst Case)

**Action**:
```bash
# Rebuild happens exactly at Monday 9am PT
docker compose build --no-cache && docker compose up -d
```

**What Happens**:
1. 9:00:00 - Cron triggers weekly report script
2. 9:00:01 - Script starts, reads SQL files
3. 9:00:02 - Script tries `docker exec db psql ...`
4. 9:00:02 - **FAIL**: Database container restarting
5. 9:00:30 - Containers fully restarted
6. Weekly report incomplete or failed

**Impact**:
- ❌ **One missed report** (that specific Monday)
- ✅ **Data still collected** (in database, not lost)
- ✅ **Next report unaffected** (following Monday)
- ✅ **Manual recovery possible** (run script manually after rebuild)

**Recovery**:
```bash
# After rebuild completes
ssh bottrader-aws "/opt/bot/weekly_strategy_review.sh"
# Result: Report generated successfully
```

**Best Practice**: Avoid rebuilding on Monday mornings between 8:50am - 9:10am PT.

---

## Update Procedures

### Updating SQL Queries (No Rebuild Required)

```bash
# Local: Edit query files
vim queries/weekly_symbol_performance.sql

# Commit and push
git add queries/*.sql
git commit -m "feat: Update symbol performance query"
git push

# AWS: Pull updates
ssh bottrader-aws "cd /opt/bot && git pull"

# Test query
ssh bottrader-aws "cat /opt/bot/queries/weekly_symbol_performance.sql"

# Test full report
ssh bottrader-aws "/opt/bot/weekly_strategy_review.sh"
```

**Container Rebuild**: ❌ **NOT REQUIRED**

---

### Updating Weekly Script (No Rebuild Required)

```bash
# Local: Edit script
vim scripts/weekly_strategy_review.sh

# Commit and push
git add scripts/weekly_strategy_review.sh
git commit -m "feat: Add new analysis section to weekly report"
git push

# AWS: Pull and update permissions
ssh bottrader-aws "cd /opt/bot && git pull && chmod +x /opt/bot/weekly_strategy_review.sh"

# Test script
ssh bottrader-aws "/opt/bot/weekly_strategy_review.sh"
```

**Container Rebuild**: ❌ **NOT REQUIRED**

---

### Updating Python Application Code (Rebuild Required)

```bash
# Local: Edit Python code
vim botreport/aws_daily_report.py

# Commit and push
git add botreport/aws_daily_report.py
git commit -m "fix: Improve P&L calculation in daily report"
git push

# AWS: Pull and rebuild affected container
ssh bottrader-aws "cd /opt/bot && git pull"
ssh bottrader-aws "docker compose --env-file /opt/bot/.env -f docker-compose.aws.yml build report-job --no-cache"
ssh bottrader-aws "docker compose -f docker-compose.aws.yml up -d report-job"
```

**Container Rebuild**: ✅ **REQUIRED** (only for `report-job`)

**Data Collection Impact**: ✅ **NONE** (weekly reports use separate system)

---

## Verification Commands

### After Any Rebuild

Run these commands to verify data collection infrastructure is intact:

```bash
# 1. Verify cron job still scheduled
ssh bottrader-aws "crontab -l | grep weekly"
# Expected: 0 9 * * 1 /opt/bot/weekly_strategy_review.sh

# 2. Verify script exists and is executable
ssh bottrader-aws "ls -la /opt/bot/weekly_strategy_review.sh"
# Expected: -rwx--x--x ... /opt/bot/weekly_strategy_review.sh

# 3. Verify SQL query files present
ssh bottrader-aws "ls -la /opt/bot/queries/*.sql"
# Expected: 3 .sql files

# 4. Verify database container running
ssh bottrader-aws "docker ps | grep db"
# Expected: Container 'db' with status 'Up'

# 5. Test database connectivity
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c 'SELECT COUNT(*) FROM trade_records;'"
# Expected: Row count returned

# 6. Test manual report generation
ssh bottrader-aws "/opt/bot/weekly_strategy_review.sh"
# Expected: Report generated successfully

# 7. Verify report file created
ssh bottrader-aws "ls -lh /opt/bot/logs/weekly_review_*.txt | tail -1"
# Expected: Most recent report file

# 8. Verify trade linkage still working
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT COUNT(*) as linked_trades
FROM trade_strategy_link
WHERE linked_at >= NOW() - INTERVAL '1 day';
\""
# Expected: Recent trades linked

# 9. Verify strategy snapshot active
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT snapshot_id, active_from, score_buy_target
FROM strategy_snapshots
WHERE active_until IS NULL;
\""
# Expected: Active snapshot returned
```

**All checks passing**: ✅ Data collection fully operational

---

## Troubleshooting

### Issue: Weekly Report Not Generated

**Symptoms**:
- No new report file in `/opt/bot/logs/`
- Expected: `weekly_review_YYYY-MM-DD.txt`

**Diagnosis**:
```bash
# Check cron job exists
ssh bottrader-aws "crontab -l | grep weekly"

# Check cron log
ssh bottrader-aws "cat /opt/bot/logs/weekly_review_cron.log"

# Check script exists
ssh bottrader-aws "ls -la /opt/bot/weekly_strategy_review.sh"

# Test manual execution
ssh bottrader-aws "/opt/bot/weekly_strategy_review.sh"
```

**Common Causes**:
1. ❌ Cron job removed - Re-add: `crontab -e`
2. ❌ Script deleted - Re-pull: `git pull`
3. ❌ Script not executable - Fix: `chmod +x /opt/bot/weekly_strategy_review.sh`
4. ❌ Database container down - Fix: `docker compose up -d db`
5. ❌ SQL files missing - Re-pull: `git pull`

---

### Issue: "docker exec db" Command Fails

**Symptoms**:
- Script runs but queries fail
- Error: `Error response from daemon: Container ... is not running`

**Diagnosis**:
```bash
# Check database container status
ssh bottrader-aws "docker ps -a | grep db"

# Check container logs
ssh bottrader-aws "docker logs db --tail 50"

# Check if container is starting
ssh bottrader-aws "docker compose -f docker-compose.aws.yml ps db"
```

**Common Causes**:
1. ❌ Container stopped - Fix: `docker compose up -d db`
2. ❌ Container restarting loop - Fix: Check logs, fix config
3. ❌ Container not in compose file - Fix: Verify docker-compose.aws.yml
4. ⏳ Container starting - Wait: 30 seconds, retry

---

### Issue: Report Empty or Incomplete

**Symptoms**:
- Report file created but has no data
- Missing sections

**Diagnosis**:
```bash
# Check if queries return data
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c 'SELECT COUNT(*) FROM fifo_allocations WHERE sell_time >= NOW() - INTERVAL \"7 days\";'"

# Check SQL query files
ssh bottrader-aws "cat /opt/bot/queries/weekly_symbol_performance.sql"

# Run script with verbose output
ssh bottrader-aws "bash -x /opt/bot/weekly_strategy_review.sh"
```

**Common Causes**:
1. ❌ No recent trades - Expected: Need 7 days of trading data
2. ❌ SQL syntax error - Fix: Review and correct SQL queries
3. ❌ Database tables missing - Fix: Run migrations
4. ❌ FIFO allocation_version wrong - Fix: Verify version=2 in queries

---

## What Can Break Data Collection

### Will NOT Break Data Collection

✅ **Safe Operations**:
- Rebuilding any Docker container
- Restarting Docker containers
- Updating Python application code
- Changing .env configuration
- Updating container environment variables
- Pulling git changes
- Deploying new application features
- Running database migrations (adds tables, doesn't delete)

### WILL Break Data Collection

❌ **Dangerous Operations**:
```bash
# DO NOT DO THESE:

# 1. Deleting weekly script
rm /opt/bot/weekly_strategy_review.sh

# 2. Deleting SQL queries
rm -rf /opt/bot/queries/

# 3. Removing cron job
crontab -r

# 4. Dropping database tables
DROP TABLE strategy_snapshots;
DROP TABLE trade_strategy_link;
DROP TABLE fifo_allocations;

# 5. Deleting external volume
docker volume rm bottrader-aws_pg_data

# 6. Deleting logs directory
rm -rf /opt/bot/logs/

# 7. Removing database container from compose file
# (removing 'db:' section from docker-compose.aws.yml)
```

**If Accidentally Broken**:
1. Restore from git: `git pull && git checkout HEAD -- <file>`
2. Re-run deployment steps from `docs/in-progress/OPTIMIZATION_DATA_COLLECTION.md`
3. Restore database from backup if tables dropped
4. Recreate cron job: See deployment guide

---

## Best Practices

### For Developers

1. **Always Use Git**
   - All host-level scripts and queries in git
   - Pull before rebuilding containers
   - Verify files after pull

2. **Avoid Monday Mornings**
   - Schedule rebuilds for Tuesday-Sunday
   - If urgent Monday rebuild needed, run between 10am-8am (avoid 9am)

3. **Test After Rebuild**
   - Run verification commands (see section above)
   - Manual test: `/opt/bot/weekly_strategy_review.sh`
   - Check most recent report generated

4. **Separate Concerns**
   - Host-level changes: No rebuild required
   - Container code changes: Rebuild required
   - Database schema changes: Migrations required

5. **Version Control Everything**
   - SQL queries in git
   - Shell scripts in git
   - Dockerfile changes in git
   - Document cron jobs in git

### For Deployments

1. **Pre-Deployment Checklist**
   ```bash
   # Verify data collection operational
   ssh bottrader-aws "/opt/bot/weekly_strategy_review.sh"

   # Note current status
   ssh bottrader-aws "docker ps"

   # Pull latest code
   ssh bottrader-aws "cd /opt/bot && git pull"
   ```

2. **During Deployment**
   ```bash
   # Rebuild containers
   ssh bottrader-aws "docker compose --env-file /opt/bot/.env -f docker-compose.aws.yml build --no-cache"

   # Restart services
   ssh bottrader-aws "docker compose -f docker-compose.aws.yml up -d"

   # Wait for health checks
   sleep 30
   ```

3. **Post-Deployment Verification**
   ```bash
   # Verify all containers running
   ssh bottrader-aws "docker ps"

   # Test data collection
   ssh bottrader-aws "/opt/bot/weekly_strategy_review.sh"

   # Verify report generated
   ssh bottrader-aws "ls -lh /opt/bot/logs/weekly_review_*.txt | tail -1"
   ```

---

## Related Documentation

### Architecture & Design
- `docs/active/architecture/FIFO_ALLOCATIONS_DESIGN.md` - P&L calculation system
- `docs/active/operations/DOCKER_DEPLOYMENT_GUIDE.md` - Container deployment procedures

### Data Collection
- `docs/in-progress/OPTIMIZATION_DATA_COLLECTION.md` - Master optimization guide
- `queries/weekly_symbol_performance.sql` - Symbol analysis query
- `queries/weekly_signal_quality.sql` - Signal quality query
- `queries/weekly_timing_analysis.sql` - Timing patterns query

### Session Documentation
- `.claude/sessions/2026-01-11-0830-deploy-optimization-data-collection.md` - Deployment session
- `.claude/sessions/2026-01-08-1150-link-trades-to-snapshots.md` - Trade linkage implementation

---

## FAQ

### Q: Can I rebuild containers without losing data collection?
**A**: Yes! Data collection runs on the host and uses external volumes. Rebuilding containers has zero impact on data collection infrastructure.

### Q: What happens if I rebuild during the Monday 9am report?
**A**: That specific report might fail or be incomplete. The data is still in the database, and you can run the script manually after the rebuild completes. Future reports will work normally.

### Q: Do I need to rebuild containers if I change SQL queries?
**A**: No. SQL queries are host files. Just `git pull` on AWS and the next report will use the updated queries.

### Q: How do I know if data collection is working?
**A**: Run `/opt/bot/weekly_strategy_review.sh` manually. If it generates a report, data collection is working. Check `/opt/bot/logs/` for historical reports.

### Q: Can I deploy new Python code without affecting data collection?
**A**: Yes! Python code is in containers. Data collection is on host. Rebuild the container with your new code and data collection continues unaffected.

### Q: What if I accidentally delete the weekly script?
**A**: Run `git pull` to restore it from version control. Then `chmod +x /opt/bot/weekly_strategy_review.sh` to make it executable.

### Q: Is trade data lost during container rebuilds?
**A**: No. Trade data is in the `pg_data` external volume which persists across container rebuilds. Only container filesystem is ephemeral.

---

## Emergency Recovery

### If All Data Collection Broken

```bash
# 1. Restore files from git
ssh bottrader-aws "cd /opt/bot && git fetch && git reset --hard origin/feature/strategy-optimization"

# 2. Restore cron job
ssh bottrader-aws "{ crontab -l 2>/dev/null | grep -v weekly; echo '0 9 * * 1 /opt/bot/weekly_strategy_review.sh >> /opt/bot/logs/weekly_review_cron.log 2>&1'; } | crontab -"

# 3. Fix permissions
ssh bottrader-aws "chmod +x /opt/bot/weekly_strategy_review.sh"

# 4. Verify database running
ssh bottrader-aws "docker compose -f docker-compose.aws.yml up -d db"

# 5. Test execution
ssh bottrader-aws "/opt/bot/weekly_strategy_review.sh"

# 6. Verify report generated
ssh bottrader-aws "ls -lh /opt/bot/logs/weekly_review_*.txt | tail -1"
```

If database data lost:
- Restore from database backup
- See `docs/active/operations/BACKUP_RESTORE_PROCEDURES.md` (if exists)
- Contact database administrator

---

**Document Maintainer**: Development Team
**Last Verified**: January 11, 2026
**Next Review**: Before any major architecture changes
