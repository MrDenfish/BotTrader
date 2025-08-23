#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 <.env file> <region> <env: dev|prod> [--include-docker] [--force] [--dry-run]"
  exit 1
}

[[ $# -lt 3 ]] && usage

ENVFILE="$1"
REGION="$2"
ENVIRONMENT="$3"; shift 3 || true

INCLUDE_DOCKER=false
FORCE=false
DRYRUN=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --include-docker) INCLUDE_DOCKER=true ;;
    --force)          FORCE=true ;;
    --dry-run)        DRYRUN=true ;;
    *) echo "unknown flag: $1"; usage ;;
  esac
  shift
done

ROOT="/bottrader/${ENVIRONMENT}"

is_db_key() {
  [[ "$1" =~ ^DB_(HOST|NAME|USER|PASSWORD|PORT|TYPE|SSLMODE|MAX_OVERFLOW|ECHO_SQL|MONITOR_INTERVAL|CONNECTION_THRESHOLD)$ ]]
}

is_docker_db_key() {
  [[ "$1" =~ ^DOCKER_DB_(HOST|NAME|USER|PASSWORD|PORT|TYPE|SSLMODE)$ ]]
}

is_alert() {
  [[ "$1" =~ ^(PHONE|ACCOUNT_PHONE|EMAIL|MY_EMAIL|E_MAILPASS|EMAIL_ALERTS|ALERT_EMAIL|ALERT_PHONE|ALERT_E_MAILPASS|ALERT_SENDER_EMAIL|ACCOUNT_EMAIL|ACCOUNT_SENDER_EMAIL)$ ]]
}

secureish() { [[ "$1" =~ (PASS|PASSWORD|SECRET|KEY)$ ]]; }

put_param() {
  local name="$1" type="$2" value="$3"
  echo "→ $name ($type)"
  $DRYRUN && return 0

  if [[ "$value" =~ ^https?:// ]] || [[ "$value" == @* ]] || [[ "$value" == file://* ]] || [[ "$value" == fileb://* ]] || printf '%s' "$value" | grep -q $'\n'; then
    local tmp; tmp="$(mktemp)"
    printf '%s' "$value" > "$tmp"
    aws ssm put-parameter --region "$REGION" --overwrite \
      --name "$name" --type "$type" --value "file://$tmp" >/dev/null
    rm -f "$tmp"
  else
    aws ssm put-parameter --region "$REGION" --overwrite \
      --name "$name" --type "$type" --value "$value" >/dev/null
  fi
}

# prod safety prompt
if [[ "$ENVIRONMENT" == "prod" && "$DRYRUN" = false ]]; then
  read -r -p "You are about to write to ${ROOT}. Continue? [y/N] " ans
  [[ "${ans,,}" == "y" ]] || { echo "aborted"; exit 1; }
fi

while IFS='=' read -r k v; do
  [[ -z "${k// }" || "$k" =~ ^# ]] && continue
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"

  path="$ROOT/app/$k"
  type="String"

  if is_db_key "$k"; then
    base="${k#DB_}"              # e.g. DB_HOST -> HOST
    path="$ROOT/db/$base"

    if [[ "$ENVIRONMENT" == "prod" && "$FORCE" == false ]]; then
      if [[ "$base" == "HOST" && "$v" == "localhost" ]]; then
        echo "skip $path: localhost not allowed in prod (use --force to override)"; continue
      fi
      if [[ "$base" == "USER" && "$v" =~ ^(Manny|postgres)$ ]]; then
        echo "skip $path: dev user not allowed in prod (use --force to override)"; continue
      fi
      if [[ "$base" == "PASSWORD" && "$v" == "yourpassword" ]]; then
        echo "skip $path: placeholder password not allowed in prod (use --force to override)"; continue
      fi
    fi

    [[ "$base" == "PASSWORD" ]] && type="SecureString"

  elif is_docker_db_key "$k"; then
    $INCLUDE_DOCKER || { echo "skip $k (docker db key; pass --include-docker to import)"; continue; }
    base="${k#DOCKER_DB_}"       # e.g. DOCKER_DB_HOST -> HOST
    path="$ROOT/docker/db/$base"
    [[ "$base" == "PASSWORD" ]] && type="SecureString"

  elif is_alert "$k"; then
    path="$ROOT/alert/$k"
    secureish "$k" && type="SecureString"

  else
    secureish "$k" && type="SecureString"
  fi

  put_param "$path" "$type" "$v"
done <"$ENVFILE"

echo "Done."

