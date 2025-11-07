# Environment Configuration Migration

## Summary

The BotTrader project has been migrated from a dual-file environment configuration system to a unified single `.env` file approach.

## Previous Setup (Deprecated)

- **`.env_tradebot`**: Used for desktop/development
- **`.env_runtime`**: Used for Docker/production
- **Problem**: 99% duplication, inconsistent mounting, complex fallback logic

## New Setup (Current)

- **`.env`**: Single unified configuration file for all environments
- **Runtime Detection**: Automatic environment detection via `running_in_docker()`
- **Dynamic Configuration**: Environment-specific values computed at runtime

## What Changed

### 1. Environment Variable Files

| Old | New | Notes |
|-----|-----|-------|
| `.env_tradebot` | `.env` | Desktop development |
| `.env_runtime` | `.env` | Docker containers |

### 2. Auto-Computed Values

The following values are now **computed automatically** based on runtime environment detection:

| Variable | Desktop Value | Docker Value |
|----------|--------------|--------------|
| `DB_HOST` | `127.0.0.1` | `db` |
| `SCORE_JSONL_PATH` | `.bottrader/cache/scores.jsonl` | `/app/logs/scores.jsonl` |
| `TP_SL_LOG_PATH` | `.bottrader/cache/tpsl.jsonl` | `/app/logs/tpsl.jsonl` |
| `WEBHOOK_BASE_URL` | From env or PC_URL | `http://webhook:5003` |

**Note**: `IN_DOCKER` and `IS_DOCKER` are no longer required in `.env` - they're auto-detected.

### 3. Files Updated

Configuration files updated to use unified approach:

- ✅ `Config/config_manager.py` - Runtime-dependent value computation
- ✅ `Shared_Utils/url_helper.py` - Uses `running_in_docker()` directly
- ✅ `docker-compose.aws.yml` - Mounts `/opt/bot/.env` for all services
- ✅ `docker/entrypoint/entrypoint.bot.sh` - Loads `/app/.env` only
- ✅ `Config/environment.py` - Updated to use `.env`
- ✅ `botreport/config.py` - Updated to use `.env`
- ✅ `docker/deploy_aws_ssh.sh` - References `.env`
- ✅ `docker/update.sh` - References `.env`
- ✅ `deploy_to_droplet.sh` - References `.env`

### 4. Runtime Detection

Environment detection uses multiple methods (in order):

1. Check for `/.dockerenv` file
2. Check `IN_DOCKER` environment variable (set by main.py)
3. Check `/proc/1/cgroup` for docker/containerd

Function: `Shared_Utils.runtime_env.running_in_docker()`

## Migration Guide

### For Existing Deployments

If you have an existing deployment with `.env_tradebot` or `.env_runtime`:

1. **On Desktop**:
   ```bash
   cd /path/to/BotTrader
   cp .env_tradebot .env
   # Or create new .env from template
   ```

2. **On Server (AWS/Droplet)**:
   ```bash
   cd /opt/bot  # or your deployment path
   cp .env_runtime .env
   # Or create new .env from template
   ```

3. **Update docker-compose paths** (if using custom paths):
   ```yaml
   volumes:
     - /opt/bot/.env:/app/.env:ro  # Not .env_runtime
   ```

### For New Deployments

1. Copy `.env` to your deployment location
2. Update environment-specific values as needed
3. The system will automatically detect and configure the environment

## Environment-Specific Overrides

If you need to override auto-detected values, you can still set them explicitly in `.env`:

```bash
# Force specific DB_HOST (overrides auto-detection)
DB_HOST=custom-db-host.example.com

# Force specific log paths (overrides auto-detection)
SCORE_JSONL_PATH=/custom/path/scores.jsonl
TP_SL_LOG_PATH=/custom/path/tpsl.jsonl
```

## Testing

### Desktop Testing

```bash
cd /home/user/BotTrader
python -c "from Config.config_manager import CentralConfig as Config; c=Config(); print(f'DB_HOST: {c.db_host}')"
# Should output: DB_HOST: 127.0.0.1
```

### Docker Testing

```bash
docker-compose -f docker-compose.aws.yml up -d webhook
docker logs webhook | grep "Auto-configured"
# Should see: 🔧 Auto-configured DB_HOST=db (is_docker=True)
```

## Benefits

1. **Single Source of Truth**: One `.env` file to maintain
2. **Reduced Duplication**: 200+ lines of duplication eliminated
3. **Simplified Deployment**: No need to manage multiple env files
4. **Clearer Logic**: Runtime detection is explicit and testable
5. **Easier Updates**: Change config once, works everywhere

## Rollback

If you need to rollback to the old dual-file system:

```bash
git revert <migration-commit-hash>
```

The old `.env_tradebot` and `.env_runtime` files remain in git history.

## Questions?

- Check `Config/config_manager.py:_compute_environment_specific_values()` for auto-config logic
- Check `Shared_Utils/runtime_env.py` for environment detection logic
- Check recent commits for detailed changes

---

**Last Updated**: 2025-11-07
**Migration Commit**: (to be filled in after commit)
