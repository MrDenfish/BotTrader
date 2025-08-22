#!/usr/bin/env bash
set -euo pipefail

: "
------------------------------------------------------------------------------
ssm-env.sh
------------------------------------------------------------------------------

Purpose:
--------
Bootstrap script for Docker containers that loads application configuration
from AWS Systems Manager (SSM) Parameter Store into environment variables.
This ensures that secrets and environment-specific settings (like database
credentials, API keys, and alerting info) are not baked into the image or
stored in local .env files.

How it works:
-------------
1. Requires two environment variables to be set before running:
   - AWS_REGION : AWS region where the SSM parameters live (e.g. us-west-2)
   - SSM_ROOT   : Base SSM path for this environment (e.g. /bottrader/prod)

2. Recursively fetches all parameters under $SSM_ROOT using (aws ssm).

3. Maps parameters to environment variables:
   - /.../db/<KEY>        → DB_<KEY>      (e.g. DB_HOST, DB_USER, DB_PASSWORD)
   - /.../docker/db/<KEY> → DOCKER_DB_<KEY>
   - /.../app/<KEY>       → <KEY>
   - /.../alert/<KEY>     → <KEY>

4. Exports the mapped values into the current shell environment so they are
   available to the Python application when it starts.

Notes:
------
- Requires (aws) CLI and (jq) inside the container.
- Passwords/secrets stored as SecureString in SSM are automatically decrypted
  before export.
- Safe to source multiple times; it will just re-export the same values.
- Intended to be sourced from the container ENTRYPOINT script before launching
  the app.

------------------------------------------------------------------------------
"

: "${AWS_REGION:?AWS_REGION is required}"
: "${SSM_ROOT:?SSM_ROOT is required}"   # e.g., /bottrader/prod or /bottrader/dev

export_ssm_path () {
  local root="$1"
  local next=""
  while :; do
    if [[ -n "$next" ]]; then
      page=(--next-token "$next")
    else
      page=()
    fi

    json="$(aws ssm get-parameters-by-path \
              --path "$root" --recursive --with-decryption \
              --region "$AWS_REGION" "${page[@]}")"

    # Export variables
    while IFS='|' read -r name value; do
      leaf="${name##*/}"
      case "$name" in
        */db/*)            export "DB_${leaf^^}"="$value" ;;
        */docker/db/*)     export "DOCKER_DB_${leaf^^}"="$value" ;;
        */app/*|*/alert/*) export "${leaf^^}"="$value" ;;
      esac
    done < <(printf '%s' "$json" | jq -r --arg root "$root/" '.Parameters[] | "\(.Name)|\(.Value)"')

    next="$(echo "$json" | jq -r '.NextToken // empty')"
    [[ -z "$next" ]] && break
  done
}

export_ssm_path "$SSM_ROOT"


