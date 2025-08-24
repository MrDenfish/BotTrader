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
    leaf="${name##*/}"
    rel="${name#${root}/}"               # e.g., app/coinbase/api_key
    rel_uc="$(echo "${rel#*/}" | tr '/[:lower:]' '_[:upper:]')"  # drop leading "app/" or "alert/"
    case "$name" in
      */db/*)            export "DB_${leaf^^}"="$value" ;;
      */docker/db/*)     export "DOCKER_DB_${leaf^^}"="$value" ;;
      */app/*|*/alert/*) export "${rel_uc}"="$value" ;;
    esac
  done

  NEXT=$(echo "$JSON" | jq -r '.NextToken // empty')
  [[ -z "$NEXT" ]] && break
done

exec "$@"
