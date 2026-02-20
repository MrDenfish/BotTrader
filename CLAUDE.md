# Claude Code Instructions for BotTrader

This file contains project-specific instructions for Claude Code.

## Deployment Process

**CRITICAL:** When deploying changes to AWS, ALWAYS use the git-based workflow, NEVER rsync.

### Production Deployment Location
- **Path:** `/opt/bot` (git repository)
- **DO NOT deploy to:** `~/BotTrader` or any other location

### Standard Deployment Steps

When asked to deploy to AWS or after committing changes:

1. **Push to GitHub:**
   ```bash
   git push origin main
   ```

2. **Deploy to AWS:**
   ```bash
   ssh bottrader-aws "cd /opt/bot && git pull origin main"
   ```

3. **Rebuild and restart v2 containers (code baked into image, restart alone won't pick up changes):**
   ```bash
   ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d --build v2-kraken"
   ```

4. **Verify deployment:**
   ```bash
   ssh bottrader-aws "cd /opt/bot && git log --oneline -3"
   ```

### What NOT to Do

- ❌ DO NOT use `rsync` to deploy code
- ❌ DO NOT deploy to `~/BotTrader`
- ❌ DO NOT manually copy files

### Quick Reference

See `.claude/DEPLOYMENT.md` for complete deployment documentation.

## Project Structure

- **Production Branch:** `main`
- **AWS Location:** `/opt/bot`
- **Docker Compose:** `docker-compose.aws.yml`

## Container Names

- `db` - PostgreSQL database
- `v2-kraken` - v2 Kraken paper trading (code baked into image — needs `--build` on deploy)
