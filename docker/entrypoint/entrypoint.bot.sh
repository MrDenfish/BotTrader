#!/usr/bin/env bash
set -euo pipefail

: "
------------------------------------------------------------------------------
entrypoint.bot.sh
------------------------------------------------------------------------------

Purpose:
--------
Container entrypoint for the trading bot. Its job is to:
1. Source (ssm-env.sh) to load environment variables from AWS SSM Parameter
   Store (database credentials, API keys, etc.).
2. Prevent accidental use of local .env files inside the container.
3. Print a sanitized snapshot of the effective DB configuration for debugging.
4. Launch the main Python application with tini as PID 1.

How it works:
-------------
- Requires AWS_REGION and SSM_ROOT to be set in the container environment.
- Sources /usr/local/bin/ssm-env (copied in during Docker build).
- Sets ALLOW_LOCAL_DOTENV=false to ensure (.env_tradebot) files are ignored.
- Validates critical DB_* env vars are present before starting.
- Prints DB_HOST, DB_PORT, DB_USER, DB_NAME, DB_SSLMODE (masks DB_PASSWORD).
- Finally execs (python -m main --run both).

Notes:
------
- Exits immediately if required env vars are missing.
- Output of DB config snapshot is for sanity checking only (never shows password).
- tini is already configured in the Dockerfile to handle signals properly.

------------------------------------------------------------------------------
"

# Required envs
: "${AWS_REGION:?AWS_REGION is required}"
: "${SSM_ROOT:?SSM_ROOT is required}"

# Load SSM params into env
. /usr/local/bin/ssm-env

# sanitize "number with inline comments" and trim whitespace for known numeric keys
sanitize_num() {
  local k="$1" v="${!1:-}"
  # strip comments and spaces
  v="$(printf '%s' "$v" | sed 's/#.*$//' | tr -d '[:space:]')"
  # optional: enforce decimal format
  if [[ -n "$v" && ! "$v" =~ ^-?[0-9]+([.][0-9]+)?$ ]]; then
    echo "[entrypoint] WARN: $k has non-numeric value '$v' after sanitization";
  fi
  export "$k=$v"
}
# add your known numeric keys here (examples)
for k in ROC_5MIN ROC_15MIN MAX_SLIPPAGE_PCT SOME_THRESHOLD; do
  sanitize_num "$k"
done

# Never load local .env files in container
export ALLOW_LOCAL_DOTENV=false

# One-time, sanitized config print for debugging
echo "[entrypoint] Effective DB env:"
for k in DB_HOST DB_PORT DB_USER DB_NAME DB_SSLMODE; do
  printf "  %s=%s\n" "$k" "${!k:-<unset>}"
done
if [[ -n "${DB_PASSWORD:-}" ]]; then echo "  DB_PASSWORD=***"; fi

# Optionally fail fast if critical vars are missing
: "${DB_HOST:?missing}"; : "${DB_USER:?missing}"; : "${DB_NAME:?missing}"

# ensure time looks sane
date -u || true

# fail fast on Coinbase creds if your app expects these (adjust names as stored in SSM):
: "${COINBASE_API_KEY:?missing}"; : "${COINBASE_API_SECRET:?missing}"; : "${COINBASE_API_PASSPHRASE:?missing}"

# Start the app
exec python -m main --run both
