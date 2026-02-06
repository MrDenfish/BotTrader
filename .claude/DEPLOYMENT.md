# BotTrader Deployment Guide

## AWS Deployment Location

**IMPORTANT:** The production code runs from `/opt/bot` on AWS, which is a git repository.

**DO NOT** use rsync or deploy to `~/BotTrader` - this causes confusion and containers won't see the changes.

---

## Standard Deployment Process

### 1. Commit and Push Changes Locally

```bash
# Stage your changes
git add <files>

# Commit with descriptive message
git commit -m "feat: Your feature description"

# Push to GitHub
git push origin main
```

### 2. Deploy to AWS

```bash
# Pull latest changes
ssh bottrader-aws "cd /opt/bot && git pull origin main"

# Restart containers
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml restart"
```

### 3. Verify Deployment

```bash
# Check commit on AWS
ssh bottrader-aws "cd /opt/bot && git log --oneline -3"

# Check container status
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml ps"

# Monitor logs
ssh bottrader-aws "docker logs webhook -f --tail 50"
```

---

## Container Information

### Active Containers
- `webhook` - Main trading webhook and WebSocket manager
- `sighook` - Signal processing and strategy execution
- `db` - PostgreSQL database
- `bottrader-report` - Daily email reporting
- `bottrader-aws-leaderboard-job-1` - Leaderboard updates

### Container Mounts
All containers mount from `/opt/bot`:
- `/opt/bot/.env` - Environment configuration
- `/opt/bot/logs` - Application logs
- `/opt/bot/webhook/` - Webhook code
- `/opt/bot/sighook/` - Sighook code

---

## Emergency Procedures

### Rollback to Previous Commit

```bash
# On AWS
ssh bottrader-aws "cd /opt/bot && git log --oneline -5"  # Find commit to rollback to
ssh bottrader-aws "cd /opt/bot && git reset --hard <commit-hash>"
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml restart"
```

### Check Container Health

```bash
# View all container logs
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml logs --tail 100"

# Check specific container
ssh bottrader-aws "docker logs webhook --tail 100"
ssh bottrader-aws "docker logs sighook --tail 100"
```

### Restart Specific Container

```bash
ssh bottrader-aws "docker restart webhook"
ssh bottrader-aws "docker restart sighook"
```

---

## Common Mistakes to Avoid

❌ **DO NOT** use rsync to deploy code
❌ **DO NOT** deploy to `~/BotTrader`
❌ **DO NOT** manually copy files to `/opt/bot`
❌ **DO NOT** forget to restart containers after pull

✅ **DO** use git pull from `/opt/bot`
✅ **DO** restart containers after deployment
✅ **DO** verify git commit after pull
✅ **DO** monitor logs after restart

---

## Quick Reference

### One-Line Deployment
```bash
git push origin main && ssh bottrader-aws "cd /opt/bot && git pull origin main && docker compose -f docker-compose.aws.yml restart"
```

### Check What's Running
```bash
ssh bottrader-aws "cd /opt/bot && git log --oneline -1 && docker compose ps"
```

---

## Notes

- AWS instance: `bottrader-aws` (SSH alias)
- Production branch: `main`
- Active development: `refactor/plugin-architecture`
- Docker Compose file: `docker-compose.aws.yml`
