#!/usr/bin/env bash
set -euo pipefail

# Require AWS_REGION (fail fast if not set)
: "${AWS_REGION:?AWS_REGION must be set (e.g. us-west-2)}"

# Where your DB params live (already created in SSM)
: "${SSM_PREFIX:=/bottrader/prod/db}"

# --- Fetch DB params ---
readarray -t DB_PARAMS < <(aws ssm get-parameters-by-path \
  --with-decryption \
  --path "$SSM_PREFIX" \
  --region "$AWS_REGION" \
  --query 'Parameters[].{Name:Name,Value:Value}' \
  --output text)

for line in "${DB_PARAMS[@]}"; do
  # output format: <Value>\t<Name>
  VAL=$(awk '{print $1}' <<< "$line")
  NAME=$(awk '{print $2}' <<< "$line")
  KEY=$(basename "$NAME" | tr '[:lower:]' '[:upper:]')  # host -> HOST
  export "DB_${KEY}=${VAL}"
done

# Derived convenience DSN
export DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:5432/${DB_NAME}"

# --- Optional email/report params ---
if aws ssm get-parameters-by-path --path "/bottrader/prod/email" \
      --with-decryption \
      --region "$AWS_REGION" \
      --query 'Parameters' \
      --output text >/dev/null 2>&1; then
  readarray -t EMAIL_PARAMS < <(aws ssm get-parameters-by-path \
    --with-decryption \
    --region "$AWS_REGION" \
    --path "/bottrader/prod/email" \
    --query 'Parameters[].{Name:Name,Value:Value}' \
    --output text)
  for line in "${EMAIL_PARAMS[@]}"; do
    VAL=$(awk '{print $1}' <<< "$line")
    NAME=$(awk '{print $2}' <<< "$line")
    KEY=$(basename "$NAME" | tr '[:lower:]' '[:upper:]')
    export "EMAIL_${KEY}=${VAL}"
  done
fi

