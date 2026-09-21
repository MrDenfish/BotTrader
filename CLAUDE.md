# Claude Code Instructions for BotTrader

This file contains project-specific instructions for Claude Code.

## Deployment Process

**CRITICAL:** When deploying changes to AWS, ALWAYS use the git-based workflow, NEVER rsync.

### Production Deployment Location
- **Path:** `/opt/bot` (git repository)
- **DO NOT deploy to:** `~/BotTrader` or any other location

### Standard Deployment Steps

When asked to deploy to AWS or after committing changes, use the deploy script (it runs
locally and does the push, pull, and rebuild for you):

```bash
./scripts/deploy_aws.sh              # v2-kraken (default) — the usual case
./scripts/deploy_aws.sh dashboard    # one other service
./scripts/deploy_aws.sh all          # rebuild + restart everything
./scripts/deploy_aws.sh --dry-run    # run the guards only, touch nothing
```

Guards built into the script (they block or ask before anything is touched):
- must be on `main` (production pulls `origin/main`)
- interactive confirmation showing host, service, commit SHA, dirty-tree warning, and the
  ~6.7h warmup-blackout cost of restarting v2-kraken; `--yes` skips it (cron/CI never hang)
- open-position check on the paper book; `--force` overrides
- every run is appended to `logs/deploys.log` (gitignored): timestamp, SHA, service, outcome

Manual equivalent (only if the script cannot run):
```bash
git push origin main
ssh bottrader-aws "cd /opt/bot && git pull --ff-only origin main"
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d --build v2-kraken"
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
