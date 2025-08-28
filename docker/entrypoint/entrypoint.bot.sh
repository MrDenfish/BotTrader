#!/usr/bin/env bash
set -Eeuo pipefail

log()  { echo "$(date '+%F %T') [entrypoint] $*"; }
warn() { echo "$(date '+%F %T') [entrypoint][WARN] $*" >&2; }
err()  { echo "$(date '+%F %T') [entrypoint][ERROR] $*" >&2; }

normalize_env_file() {
  local f="$1"
  if [[ -f "$f" ]]; then
    # strip CRLF if present
    sed -i 's/\r$//' "$f" || true
  fi
}

export_env_file() {
  local f="$1"
  if [[ -f "$f" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "$f"
    set +a
    log "Loaded env from $f (exported to process)."
  else
    warn "Env file $f not found; continuing with existing env only."
  fi
}

infer_static_ip() {
  # Only set if not already provided
  if [[ -z "${DOCKER_STATICIP:-}" ]]; then
    local ip=""
    ip="$(curl -fsS http://169.254.169.254/latest/meta-data/public-ipv4 || true)"
    [[ -z "$ip" ]] && ip="$(curl -fsS https://checkip.amazonaws.com || true)"
    if [[ -n "$ip" ]]; then
      export DOCKER_STATICIP="$ip"
      log "Inferred DOCKER_STATICIP=$DOCKER_STATICIP"
    else
      warn "Could not infer public IP for DOCKER_STATICIP"
    fi
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
    # bash /dev/tcp check
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

sanity_print() {
  local base="${COINBASE_API_BASE_URL:-unset}"
  local prefix="${COINBASE_API_PREFIX:-unset}"
  local sandbox="${COINBASE_USE_SANDBOX:-unset}"
  local keylen="${#COINBASE_API_KEY:-0}"
  local seclen="${#COINBASE_API_SECRET:-0}"
  local pplen="${#COINBASE_API_PASSPHRASE:-0}"
  log "Coinbase cfg base=${base} prefix=${prefix} sandbox=${sandbox} key_len=${keylen} secret_len=${seclen} pp_len=${pplen}"
  log "DB cfg host=${POSTGRES_HOST:-db} port=${POSTGRES_PORT:-5432} db=${POSTGRES_DB:-bot_trader_db} user=${POSTGRES_USER:-bottrader}"
}

check_required() {
  # Toggle strict mode with REQUIRED_ENV_STRICT=1
  local required=(
    POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD
    COINBASE_API_KEY COINBASE_API_SECRET COINBASE_API_PASSPHRASE
    COINBASE_API_BASE_URL COINBASE_API_PREFIX
  )
  local missing=()
  for k in "${required[@]}"; do
    if [[ -z "${!k:-}" ]]; then missing+=("$k"); fi
  done
  if ((${#missing[@]})); then
    if [[ "${REQUIRED_ENV_STRICT:-0}" == "1" ]]; then
      err "Missing required env keys: ${missing[*]}"
      exit 1
    else
      warn "Missing env keys (continuing): ${missing[*]}"
    fi
  fi
}

start_app() {
  export PYTHONUNBUFFERED=1
  if [[ "${DEBUGPY:-0}" == "1" ]]; then
    local port="${DEBUGPY_PORT:-5678}"
    log "Starting under debugpy on 0.0.0.0:${port} (waiting for IDE attach)..."
    exec python -X dev -m debugpy --listen 0.0.0.0:"${port}" --wait-for-client -m main
  else
    exec python -m main
  fi
}

### ---- main ----

log "BotTrader starting (Option A)..."

# 1) Ensure the app dotenv is exported *before* Python starts
normalize_env_file "/app/.env_tradebot"
export_env_file    "/app/.env_tradebot"

# 2) Try to populate DOCKER_STATICIP (useful for IP allow-lists)
infer_static_ip

# 3) Wait for Postgres to be reachable
wait_for_postgres

# 4) Print a sanitized snapshot for visibility at T=0
sanity_print

# 5) Optionally fail fast if required keys are missing
check_required

# 6) Go!
start_app


