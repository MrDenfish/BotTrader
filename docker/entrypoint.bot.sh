#!/usr/bin/env bash
set -Eeuo pipefail

: "${AWS_REGION:?AWS_REGION must be set}"
export AWS_DEFAULT_REGION="$AWS_REGION" AWS_PAGER="" AWS_CLI_PAGER=""

# source env (don’t die on unset inside ssm-env)
set +u; . /usr/local/bin/ssm-env; set -u

echo "[entrypoint] user=${DB_USER:-?} host=${DB_HOST:-?} port=${DB_PORT:-?} db=${DB_NAME:-?} webhook=${WEBHOOK_PORT:-?}"

# Let RUN_MODE override for quick tests (webhook|both)
: "${RUN_MODE:=both}"
exec python -m main --run "${RUN_MODE}"