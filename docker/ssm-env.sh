#!/usr/bin/env bash
set -euo pipefail

: "${AWS_REGION:?AWS_REGION must be set (e.g. us-west-2)}"
export AWS_DEFAULT_REGION="$AWS_REGION" AWS_PAGER="" AWS_CLI_PAGER=""

# export NAME=VALUE with normalized NAME
_export() {
  local k="$1" v="$2"
  k="$(echo "$k" | tr '[:lower:]' '[:upper:]' | sed 's/[^A-Z0-9_]/_/g')"
  export "${k}=${v}"
}

# Fetch pairs under PATH and export with a policy:
#   policy=db   -> DB_<KEY>
#   policy=raw  -> <KEY>
#   policy=pre:FOO_ -> FOO_<KEY>
_fetch_with_policy() {
  local policy="$1" path="$2"
  mapfile -t L < <(aws ssm get-parameters-by-path \
      --with-decryption \
      --path "$path" \
      --region "$AWS_REGION" \
      --query 'Parameters[*].[Name,Value]' \
      --output text 2>/dev/null || true)
  for line in "${L[@]:-}"; do
    [ -n "$line" ] || continue
    local name="${line%%$'\t'*}"
    local val="${line#*$'\t'}"
    local base="$(basename "$name")"
    [ -n "$base" ] && [ -n "$val" ] || continue
    [[ "$base" =~ _$ ]] && continue
    case "$policy" in
      db)     _export "DB_${base}" "$val" ;;
      raw)    _export "${base}"     "$val" ;;
      pre:*)  _export "${policy#pre:}${base}" "$val" ;;
    esac
  done
}

# Pull in parameters
_fetch_with_policy db         "/bottrader/prod/db"
_fetch_with_policy raw        "/bottrader/prod/app"
_fetch_with_policy pre:EMAIL_ "/bottrader/prod/email"
_fetch_with_policy pre:ALERT_ "/bottrader/prod/alert"

# Defaults
: "${DB_PORT:=5432}"; export DB_PORT
: "${WEBHOOK_PORT:=5003}"; export WEBHOOK_PORT
: "${DOCKER_URL:=http://webhook:${WEBHOOK_PORT}/webhook}"; export DOCKER_URL
export RUNNING_IN_DOCKER=true

# Canonicalize alert/email names the code may expect
[ -n "${ALERT_PHONE:-}" ]            && export PHONE="${PHONE:-$ALERT_PHONE}"
[ -n "${ACCOUNT_PHONE:-}" ]          && export PHONE="${PHONE:-$ACCOUNT_PHONE}"
[ -n "${ALERT_EMAIL:-}" ]            && export EMAIL="${EMAIL:-$ALERT_EMAIL}"
[ -n "${ACCOUNT_EMAIL:-}" ]          && export EMAIL="${EMAIL:-$ACCOUNT_EMAIL}"
[ -n "${ALERT_E_MAILPASS:-}" ]       && export E_MAILPASS="${E_MAILPASS:-$ALERT_E_MAILPASS}"
[ -n "${EMAIL_PASSWORD:-}" ]         && export E_MAILPASS="${E_MAILPASS:-$EMAIL_PASSWORD}"
[ -n "${ALERT_MY_EMAIL:-}" ]       && export MY_EMAIL="${MY_EMAIL:-$ALERT_MY_EMAIL}"
[ -n "${ACCOUNT_MY_EMAIL:-}" ]     && export MY_EMAIL="${MY_EMAIL:-$ACCOUNT_MY_EMAIL}"

[ -n "${ALERT_SENDER_EMAIL:-}" ]     && export MY_EMAIL="${MY_EMAIL:-$ALERT_SENDER_EMAIL}"
[ -n "${ACCOUNT_SENDER_EMAIL:-}" ]   && export MY_EMAIL="${MY_EMAIL:-$ACCOUNT_SENDER_EMAIL}"

# Mirror into the names CentralConfig uses in Docker
export DOCKER_DB_HOST="${DB_HOST:-}"
export DOCKER_DB_USER="${DB_USER:-}"

# DSN
if [ -n "${DB_USER:-}" ] && [ -n "${DB_PASSWORD:-}" ] && [ -n "${DB_HOST:-}" ] && [ -n "${DB_NAME:-}" ]; then
  export DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
fi

# Optional: write coinbase JSONs if present
mkdir -p /app/Config
val="$(aws ssm get-parameter --region "$AWS_REGION" \
  --name /bottrader/prod/coinbase/webhook_api_key_json \
  --with-decryption --query Parameter.Value --output text 2>/dev/null || true)"
[ -n "${val:-}" ] && [ "${val}" != "None" ] && printf '%s' "$val" > /app/Config/webhook_api_key.json

val="$(aws ssm get-parameter --region "$AWS_REGION" \
  --name /bottrader/prod/coinbase/websocket_api_info_json \
  --with-decryption --query Parameter.Value --output text 2>/dev/null || true)"
[ -n "${val:-}" ] && [ "${val}" != "None" ] && printf '%s' "$val" > /app/Config/websocket_api_info.json

echo "[ssm-env] DB_HOST=${DB_HOST:-?} DB_NAME=${DB_NAME:-?} DB_USER=${DB_USER:-?} DB_PORT=${DB_PORT:-?}"

