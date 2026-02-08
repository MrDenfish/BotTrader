#!/usr/bin/env bash
set -Eeuo pipefail

log()  { echo "$(date '+%F %T') [v2-entrypoint] $*"; }
warn() { echo "$(date '+%F %T') [v2-entrypoint][WARN] $*" >&2; }
err()  { echo "$(date '+%F %T') [v2-entrypoint][ERROR] $*" >&2; }

export_env_file_ro() {
  local f="$1"
  if [[ -f "$f" ]]; then
    local tmp
    tmp="$(mktemp)"
    sed 's/\r$//' "$f" > "$tmp"
    set -a
    # shellcheck disable=SC1090
    . "$tmp"
    set +a
    rm -f "$tmp"
    log "Loaded env from $f"
  else
    warn "Env file $f not found; continuing with existing env only."
  fi
}

wait_for_postgres() {
  local host="${POSTGRES_HOST:-db}"
  local port="${POSTGRES_PORT:-5432}"
  local timeout="${WAIT_FOR_DB_TIMEOUT:-60}"

  log "Waiting for Postgres at ${host}:${port} (timeout ${timeout}s)..."
  local start now
  start=$(date +%s)
  while true; do
    if (echo >/dev/tcp/"$host"/"$port") >/dev/null 2>&1; then
      log "Postgres is reachable."
      break
    fi
    sleep 1
    now=$(date +%s)
    if (( now - start > timeout )); then
      err "Timed out waiting for Postgres at ${host}:${port}"
      exit 1
    fi
  done
}

# -------------------- main --------------------

log "BotTrader v2 (paper mode) starting..."

# 1) Load .env
if [ -f /app/.env ]; then
  export_env_file_ro "/app/.env"
else
  warn "No .env file found under /app; continuing with existing env only."
fi

# 2) DB env compat shim: map DB_* -> POSTGRES_* if not set
: "${DB_HOST:=db}"
: "${DB_PORT:=5432}"
: "${DB_NAME:=bot_trader_db}"
: "${DB_USER:=bot_user}"
: "${DB_PASSWORD:=changeme}"

: "${POSTGRES_HOST:=$DB_HOST}"
: "${POSTGRES_PORT:=$DB_PORT}"
: "${POSTGRES_DB:=$DB_NAME}"
: "${POSTGRES_USER:=bot_user}"
: "${POSTGRES_PASSWORD:=$DB_PASSWORD}"

# Build DATABASE_URL with plain postgresql:// (asyncpg, not SQLAlchemy)
if [[ -z "${DATABASE_URL-}" || -z "$DATABASE_URL" ]]; then
  export DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
fi
export POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD DATABASE_URL

# 3) Wait for Postgres
wait_for_postgres

# 4) Create log directories
mkdir -p /app/logs/v2
chmod -R 0775 /app/logs/v2 || true

# 5) Launch v2 in paper mode
log "Starting v2 paper trading..."
exec python -u -m v2 --config /app/v2/paper_trading.yaml --log-level "${LOG_LEVEL:-INFO}"
