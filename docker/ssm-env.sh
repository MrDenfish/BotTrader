#!/usr/bin/env bash
set -euo pipefail

: "${AWS_REGION:?AWS_REGION must be set (e.g. us-west-2)}"
export AWS_DEFAULT_REGION="$AWS_REGION" AWS_PAGER="" AWS_CLI_PAGER=""

_export() { local k="$1" v="$2"; k="$(echo "$k" | tr '[:lower:]' '[:upper:]' | sed 's/[^A-Z0-9_]/_/g')"; export "${k}=${v}"; }

_fetch_with_policy() {
  local policy="$1" path="$2"
  mapfile -t L < <(aws ssm get-parameters-by-path \
      --with-decryption --path "$path" --region "$AWS_REGION" \
      --query 'Parameters[*].[Name,Value]' --output text 2>/dev/null || true)
  for line in "${L[@]:-}"; do
    [ -n "$line" ] || continue
    local name="${line%%$'\t'*}" val="${line#*$'\t'}" base="$(basename "$name")"
    [ -n "$base" ] && [ -n "$val" ] || continue
    [[ "$base" =~ _$ ]] && continue
    case "$policy" in
      db)    _export "DB_${base}" "$val" ;;
      raw)   _export "${base}"     "$val" ;;
      pre:*) _export "${policy#pre:}${base}" "$val" ;;
    esac
  done
}

# Pull in params
_fetch_with_policy db   "/bottrader/prod/db"
_fetch_with_policy raw  "/bottrader/prod/app"
_fetch_with_policy raw  "/bottrader/prod/alert"
_fetch_with_policy pre:EMAIL_ "/bottrader/prod/email"

# Back-compat / aliasing for alert creds
[ -n "${ALERT_PHONE:-}" ]          && export PHONE="$ALERT_PHONE"
[ -n "${ALERT_EMAIL:-}" ]          && export EMAIL="$ALERT_EMAIL"
[ -n "${ALERT_E_MAILPASS:-}" ]     && export E_MAILPASS="$ALERT_E_MAILPASS"
[ -n "${ALERT_MY_EMAIL:-}" ]       && export MY_EMAIL="$ALERT_MY_EMAIL"

[ -n "${ACCOUNT_PHONE:-}" ]        && export PHONE="$ACCOUNT_PHONE"
[ -n "${ACCOUNT_EMAIL:-}" ]        && export EMAIL="$ACCOUNT_EMAIL"
[ -n "${ACCOUNT_EMAIL_PASS:-}" ]   && export E_MAILPASS="$ACCOUNT_EMAIL_PASS"
[ -n "${ALERT_SENDER_EMAIL:-}" ]   && export MY_EMAIL="$ALERT_SENDER_EMAIL"

: "${DB_PORT:=5432}"; export DB_PORT
: "${WEBHOOK_PORT:=5003}"; export WEBHOOK_PORT
export RUNNING_IN_DOCKER=true

# Mirror for CentralConfig (docker branch)
export DOCKER_DB_HOST="${DOCKER_DB_HOST:-${DB_HOST:-}}"
export DOCKER_DB_USER="${DOCKER_DB_USER:-${DB_USER:-}}"

# DSN
if [ -n "${DB_USER:-}" ] && [ -n "${DB_PASSWORD:-}" ] && [ -n "${DB_HOST:-}" ] && [ -n "${DB_NAME:-}" ]; then
  export DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
fi

# Refresh runtime .env every boot so dotenv picks up SSM values
cat >/app/.env_runtime <<EOF
db_host=${DB_HOST:-}
db_name=${DB_NAME:-}
db_user=${DB_USER:-}
db_password=${DB_PASSWORD:-}
db_port=${DB_PORT:-}
DATABASE_URL=${DATABASE_URL:-}
RUNNING_IN_DOCKER=true
DOCKER_DB_HOST=${DOCKER_DB_HOST:-}
DOCKER_DB_USER=${DOCKER_DB_USER:-}
WEBHOOK_PORT=${WEBHOOK_PORT:-}
DOCKER_URL=${DOCKER_URL:-http://webhook:${WEBHOOK_PORT}/webhook}

# Alert knobs (safe defaults)
EMAIL_ALERTS=${EMAIL_ALERTS:-false}
PHONE=${PHONE:-}
EMAIL=${EMAIL:-}
E_MAILPASS=${E_MAILPASS:-}
MY_EMAIL=${MY_EMAIL:-}

# Common app knobs you’ve needed
MIN_VALUE_TO_MONITOR=${MIN_VALUE_TO_MONITOR:-}
MIN_BUY_VALUE=${MIN_BUY_VALUE:-}
MIN_SELL_VALUE=${MIN_SELL_VALUE:-}
TRAILING_STOP=${TRAILING_STOP:-}
TRAILING_LIMIT=${TRAILING_LIMIT:-}
EOF
ln -snf /app/.env_runtime /app/.env_tradebot

# Coinbase JSONs
for pair in webhook_api_key_json:/app/Config/webhook_api_key.json websocket_api_info_json:/app/Config/websocket_api_info.json; do
  key="${pair%%:*}"; out="${pair#*:}"
  val="$(aws ssm get-parameter --region "$AWS_REGION" --name "/bottrader/prod/coinbase/${key}" --with-decryption --query Parameter.Value --output text 2>/dev/null || true)"
  if [ -n "${val:-}" ] && [ "${val}" != "None" ]; then printf '%s' "$val" > "$out"; echo "[ssm-env] wrote ${out}"; fi
done

echo "[ssm-env] DB_HOST=${DB_HOST:-?} DB_NAME=${DB_NAME:-?} DB_USER=${DB_USER:-?} DB_PORT=${DB_PORT:-?} EMAIL_ALERTS=${EMAIL_ALERTS:-false}"









