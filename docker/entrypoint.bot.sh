#!/usr/bin/env bash
set -euo pipefail

: "${AWS_REGION:?AWS_REGION must be set}"
export AWS_DEFAULT_REGION="$AWS_REGION" AWS_PAGER="" AWS_CLI_PAGER=""

# Source ssm-env so exports land in THIS shell (temporarily disable nounset)
set +u
. /usr/local/bin/ssm-env
set -u

echo "[entrypoint] user=${DB_USER:-?} host=${DB_HOST:-?} port=${DB_PORT:-?} db=${DB_NAME:-?}"
exec python -m main --run both