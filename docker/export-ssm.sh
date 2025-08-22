#!/usr/bin/env bash
set -euo pipefail

: "${AWS_REGION:?missing AWS_REGION}"
: "${SSM_ROOT:?missing SSM_ROOT}"   # e.g., /bottrader/prod

# Pull all params under SSM_ROOT and export to env
TOKEN=""
NEXT=""
while :; do
  if [[ -n "${NEXT}" ]]; then
    TOKEN=(--next-token "$NEXT")
  else
    TOKEN=()
  fi

  JSON=$(aws ssm get-parameters-by-path \
            --path "$SSM_ROOT" --recursive --with-decryption \
            --region "$AWS_REGION" "${TOKEN[@]}")

  # Export k=v for each parameter (strip the /bottrader/prod/ prefix)
  echo "$JSON" | jq -r --arg root "$SSM_ROOT/" '
    .Parameters[] | "\(.Name)|\(.Value)" ' | \
  while IFS='|' read -r name value; do
    key="${name#$SSM_ROOT/}"     # remove prefix
    key="${key^^}"               # upper-case, if you prefer
    key="${key##*/}"             # keep the leaf (e.g., db/HOST -> HOST)
    case "$name" in
      */db/*)
        export "DB_${key}"="$value"
        ;;
      */docker/db/*)
        export "DOCKER_DB_${key}"="$value"
        ;;
      */app/*)
        # app keys: export as their leaf name
        export "$key"="$value"
        ;;
      */alert/*)
        export "$key"="$value"
        ;;
    esac
  done

  NEXT=$(echo "$JSON" | jq -r '.NextToken // empty')
  [[ -z "$NEXT" ]] && break
done

exec "$@"
