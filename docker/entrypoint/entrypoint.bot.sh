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

: "${AWS_REGION:?AWS_REGION is required}"
: "${SSM_ROOT:?SSM_ROOT is required}"

# --- 1) Try helper script (both eval(stdout) and source) ---
if [[ -x /usr/local/bin/ssm-env.sh ]]; then
  out="$(/usr/local/bin/ssm-env.sh 2>/dev/null || true)"
  [[ -n "${out}" ]] && eval "${out}" || true
  [[ -z "${DB_HOST:-}" ]] && . /usr/local/bin/ssm-env.sh || true
fi

# --- 2) Hard fallback: read SSM directly and export DB_*, ROC_5MIN, etc. ---
if [[ -z "${DB_HOST:-}" ]]; then
  echo "[entrypoint] Fallback: pulling env from SSM ${SSM_ROOT}"
  json="$(AWS_PAGER= aws ssm get-parameters-by-path \
            --region "${AWS_REGION}" \
            --path "${SSM_ROOT}" \
            --recursive --with-decryption --output json 2>/dev/null || true)"
  if [[ -n "${json}" ]]; then
    while IFS='=' read -r k v; do
      [[ -n "${k}" ]] && export "${k}=${v}"
    done < <(
      echo "${json}" | jq -r '
        .Parameters[] | {n:.Name, v:.Value} |
        # normalize to env names
        if (.n|test("/app/DB_HOST$"))       then "DB_HOST=\(.v)"
        elif (.n|test("/app/DB_PORT$"))      then "DB_PORT=\(.v)"
        elif (.n|test("/app/DB_NAME$"))      then "DB_NAME=\(.v)"
        elif (.n|test("/app/DB_USER$"))      then "DB_USER=\(.v)"
        elif (.n|test("/app/DB_PASSWORD$"))  then "DB_PASSWORD=\(.v)"
        elif (.n|test("/app/DB_SSLMODE$"))   then "DB_SSLMODE=\(.v)"
        # also accept hierarchical /db/* in case those are set
        elif (.n|test("/db/HOST$"))          then "DB_HOST=\(.v)"
        elif (.n|test("/db/PORT$"))          then "DB_PORT=\(.v)"
        elif (.n|test("/db/NAME$"))          then "DB_NAME=\(.v)"
        elif (.n|test("/db/USER$"))          then "DB_USER=\(.v)"
        elif (.n|test("/db/PASSWORD$"))      then "DB_PASSWORD=\(.v)"
        elif (.n|test("/db/SSLMODE$"))       then "DB_SSLMODE=\(.v)"
        # a couple of known app keys with case pitfalls
        elif (.n|test("/app/ROC_5MIN$"))     then "ROC_5MIN=\(.v)"
        elif (.n|test("/app/ROC_5min$"))     then "ROC_5MIN=\(.v)"
        else empty end
      '
    )
  fi
fi

# sanitize a few numeric keys (optional)
sanitize_num() {
  local k="$1" v="${!1:-}"
  v="$(printf '%s' "$v" | sed 's/#.*$//' | tr -d '[:space:]')"
  export "$k=$v"
}
for k in ROC_5MIN ROC_15MIN MAX_SLIPPAGE_PCT SOME_THRESHOLD; do sanitize_num "$k"; done

export ALLOW_LOCAL_DOTENV=false

echo "[entrypoint] Effective DB env:"
for k in DB_HOST DB_PORT DB_USER DB_NAME DB_SSLMODE; do
  printf "  %s=%s\n" "$k" "${!k:-<unset>}"
done
[[ -n "${DB_PASSWORD:-}" ]] && echo "  DB_PASSWORD=***"

# Fail fast only on the DB for now (comment API checks while debugging)
: "${DB_HOST:?missing}"
: "${DB_USER:?missing}"
: "${DB_NAME:?missing}"

date -u || true

# Re-enable these later after DB is up
# : "${COINBASE_API_KEY:?missing}"
# : "${COINBASE_API_SECRET:?missing}"
# : "${COINBASE_API_PASSPHRASE:?missing}"

exec python -m main --run both
