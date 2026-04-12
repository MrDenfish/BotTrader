#!/usr/bin/env bash
set -Eeuo pipefail

log()  { echo "$(date '+%F %T') [dashboard] $*"; }
warn() { echo "$(date '+%F %T') [dashboard][WARN] $*" >&2; }
err()  { echo "$(date '+%F %T') [dashboard][ERROR] $*" >&2; }

# -------------------- Load .env --------------------
if [ -f /app/.env ]; then
  tmp="$(mktemp)"
  sed 's/\r$//' /app/.env > "$tmp"
  set -a
  # shellcheck disable=SC1090
  . "$tmp"
  set +a
  rm -f "$tmp"
  log "Loaded env from /app/.env"
else
  warn "No .env file found; continuing with existing env only."
fi

# -------------------- Build DATABASE_URL if not set --------------------
: "${DB_HOST:=db}"
: "${DB_PORT:=5432}"
: "${DB_NAME:=bot_trader_db}"
: "${DB_USER:=bot_user}"
: "${DB_PASSWORD:=changeme}"

if [[ -z "${DATABASE_URL-}" || -z "$DATABASE_URL" ]]; then
  export DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
fi

# -------------------- Wait for Postgres (Python TCP check) --------------------
log "Waiting for Postgres at ${DB_HOST}:${DB_PORT}..."

python3 -c "
import socket, time, sys, os
host = os.environ.get('DB_HOST', 'db')
port = int(os.environ.get('DB_PORT', '5432'))
for attempt in range(1, 31):
    try:
        s = socket.create_connection((host, port), timeout=2)
        s.close()
        print(f'Postgres reachable at {host}:{port}')
        sys.exit(0)
    except OSError:
        print(f'Waiting for Postgres... ({attempt}/30)')
        time.sleep(2)
print(f'Timed out waiting for Postgres at {host}:{port}', file=sys.stderr)
sys.exit(1)
"

# -------------------- Launch Streamlit --------------------
log "Starting Streamlit dashboard..."
exec streamlit run /app/v2/dashboard/app.py
