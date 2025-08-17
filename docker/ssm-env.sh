#!/usr/bin/env bash
set -euo pipefail

# Require region; mirror for SDKs/CLI
: "${AWS_REGION:?AWS_REGION must be set (e.g. us-west-2)}"
export AWS_DEFAULT_REGION="$AWS_REGION"

# Disable AWS CLI pager
export AWS_PAGER=""
export AWS_CLI_PAGER=""

# Prefix for DB params
: "${SSM_PREFIX:=/bottrader/prod/db}"

sanitize_key() {
  printf '%s' "$1" | tr '[:lower:]' '[:upper:]' | sed 's/[^A-Z0-9_]/_/g'
}

# Try by-path first; fallback to individual keys if needed
set +e
DB_LINES="$(aws ssm get-parameters-by-path \
  --with-decryption \
  --path "$SSM_PREFIX" \
  --region "$AWS_REGION" \
  --query 'Parameters[*].[Name,Value]' \
  --output text 2>/dev/null)"
rc=$?
set -e

if [ $rc -eq 0 ] && [ -n "$DB_LINES" ]; then
  while IFS=$'\t' read -r NAME VAL; do
    [ -z "${NAME:-}" ] && continue
    KEY="$(sanitize_key "$(basename "$NAME")")"
    export "DB_${KEY}=${VAL}"
  done <<< "$DB_LINES"
else
  for k in host name user password port; do
    set +e
    VAL="$(aws ssm get-parameter \
      --name "${SSM_PREFIX}/${k}" \
      --with-decryption \
      --region "$AWS_REGION" \
      --query 'Parameter.Value' \
      --output text 2>/dev/null)"
    rc2=$?
    set -e
    if [ $rc2 -eq 0 ] && [ -n "$VAL" ] && [ "$VAL" != "None" ]; then
      KEY="$(sanitize_key "$k")"
      export "DB_${KEY}=${VAL}"
    fi
  done
fi

: "${DB_PORT:=5432}"
export DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"

# Optional: email/report params
set +e
EMAIL_LINES="$(aws ssm get-parameters-by-path \
  --with-decryption \
  --path "/bottrader/prod/email" \
  --region "$AWS_REGION" \
  --query 'Parameters[*].[Name,Value]' \
  --output text 2>/dev/null)"
set -e
if [ -n "$EMAIL_LINES" ]; then
  while IFS=$'\t' read -r NAME VAL; do
    [ -z "${NAME:-}" ] && continue
    KEY="$(sanitize_key "$(basename "$NAME")")"
    export "EMAIL_${KEY}=${VAL}"
  done <<< "$EMAIL_LINES"
fi


