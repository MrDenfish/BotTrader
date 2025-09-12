#!/usr/bin/env bash
set -Eeuo pipefail

log()  { echo "$(date '+%F %T') [entrypoint] $*"; }
warn() { echo "$(date '+%F %T') [entrypoint][WARN] $*" >&2; }
err()  { echo "$(date '+%F %T') [entrypoint][ERROR] $*" >&2; }

export_env_file_ro() {
  # Read a read-only env file safely (no in-place edits)
  local f="$1"
  if [[ -f "$f" ]]; then
    local tmp
    tmp="$(mktemp)"
    # Strip CRLF into a temp file we own
    sed 's/\r$//' "$f" > "$tmp"
    set -a
    # shellcheck disable=SC1090
    . "$tmp"
    set +a
    rm -f "$tmp"
    log "Loaded env from $f (exported to process)."
  else
    warn "Env file $f not found; continuing with existing env only."
  fi
}

infer_static_ip() {
  if [[ -z "${DOCKER_STATICIP-}" || -z "${DOCKER_STATICIP}" ]]; then
    local ip="" token=""
# Try IMDSv2 (fast timeout, no noisy errors)
    token="$(curl -sS --connect-timeout 1 -m 1 -X PUT \
      "http://169.254.169.254/latest/api/token" \
        -H "X-aws-ec2-metadata-token-ttl-seconds: 60" || true)"
    if [[ -n "$token" ]]; then
      ip="$(curl -sS --connect-timeout 1 -m 1 \
        -H "X-aws-ec2-metadata-token: $token" \
        http://169.254.169.254/latest/meta-data/public-ipv4 || true)"
    fi
    # Quiet fallbacks
    if [[ -z "$ip" ]]; then
      ip="$(dig +short myip.opendns.com @resolver1.opendns.com 2>/dev/null || true)"
    fi
    if [[ -z "$ip" ]]; then
      ip="$(curl -sS --connect-timeout 1 -m 1 https://checkip.amazonaws.com 2>/dev/null || true)"
    fi
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
  # Use safe fallbacks for length when vars might be unset
  local _key="${COINBASE_API_KEY-}"
  local _sec="${COINBASE_API_SECRET-}"
  local _pp="${COINBASE_API_PASSPHRASE-}"
  local keylen="${#_key}"
  local seclen="${#_sec}"
  local pplen="${#_pp}"

  local base="${COINBASE_API_BASE_URL:-unset}"
  local prefix="${COINBASE_API_PREFIX:-unset}"
  local sandbox="${COINBASE_USE_SANDBOX:-unset}"
  log "Coinbase cfg base=${base} prefix=${prefix} sandbox=${sandbox} key_len=${keylen} secret_len=${seclen} pp_len=${pplen}"
  log "DB cfg host=${POSTGRES_HOST:-db} port=${POSTGRES_PORT:-5432} db=${POSTGRES_DB:-bot_trader_db} user=${POSTGRES_USER:-bot_user}"
}

check_required() {
  # Toggle strict mode with REQUIRED_ENV_STRICT=1
  local required=(
    POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD
  )
  local missing=()
  for k in "${required[@]}"; do
    if [[ -z "${!k-}" || -z "${!k}" ]]; then
      missing+=("$k")
    fi
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
  export PYTHONFAULTHANDLER=1     # add this line

  local mode="${RUN_MODE:-both}"   # default keeps desktop behavior

  if [[ "${DEBUGPY:-0}" == "1" ]]; then
    local port="${DEBUGPY_PORT:-5678}"
    log "Starting under debugpy (mode=${mode})..."
    # add -u here ↓
    exec python -u -X dev -m debugpy --listen 0.0.0.0:"${port}" --wait-for-client -m main --run "${mode}"
  else
    log "Starting main (mode=${mode})..."
    # add -u here ↓
    exec python -u -m main --run "${mode}"
  fi
}

# -------------------- main --------------------

log "BotTrader starting (Option A)..."

# 1) Export env from the bind-mounted file (read-only safe)
#     Prefer .env_runtime in containers; fallback to .env_tradebot for legacy.
if [ -f /app/.env_runtime ]; then
  export_env_file_ro "/app/.env_runtime"
elif [ -f /app/.env_tradebot ]; then
  export_env_file_ro "/app/.env_tradebot"
else
  warn "No .env_runtime or .env_tradebot found under /app; continuing with existing env."
fi
# --- compat shim: map DB_* -> POSTGRES_* if POSTGRES_* not set ---
: "${DB_HOST:=db}"
: "${DB_PORT:=5432}"
: "${DB_NAME:=bot_trader_db}"
: "${DB_USER:=bot_user}"
: "${DB_PASSWORD:=changeme}"

: "${POSTGRES_HOST:=$DB_HOST}"
: "${POSTGRES_PORT:=$DB_PORT}"
: "${POSTGRES_DB:=$DB_NAME}"
: "${POSTGRES_USER:=$DB_USER}"
: "${POSTGRES_PASSWORD:=$DB_PASSWORD}"

if [[ -z "${DATABASE_URL-}" || -z "$DATABASE_URL" ]]; then
  export DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
fi
export POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD DATABASE_URL
# --- end compat shim ---


# 2) Try to populate DOCKER_STATICIP (useful if your API key is IP-allowlisted)
infer_static_ip

# 3) Wait for Postgres
wait_for_postgres

# 4) Print a sanitized snapshot for visibility at T=0
sanity_print

# 5) Optionally fail fast if required keys are missing
check_required

# 6) Launch
start_app



