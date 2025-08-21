#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "usage: $0 <.env file> <region> [prefix=/bottrader/prod]"
  exit 1
fi

ENVFILE="$1"
REGION="$2"
ROOT="${3:-/bottrader/prod}"

# --- helpers --------------------------------------------------------------

# Decide where a key goes
is_db()     { [[ "$1" =~ ^(DB_HOST|DB_NAME|DB_USER|DB_PASSWORD|DB_PORT|DB_TYPE|TYPE|NAME|USER|PASSWORD|HOST|PORT|MAX_OVERFLOW|ECHO_SQL|MONITOR_INTERVAL|CONNECTION_THRESHOLD)$ ]]; }
is_alert()  { [[ "$1" =~ ^(PHONE|ACCOUNT_PHONE|EMAIL|MY_EMAIL|E_MAILPASS|EMAIL_ALERTS|ALERT_EMAIL|ALERT_PHONE|ALERT_E_MAILPASS|ALERT_SENDER_EMAIL|ACCOUNT_EMAIL|ACCOUNT_SENDER_EMAIL)$ ]]; }

# Put parameter safely (handles http(s):// and multi-line values)
put_param() {
  local name="$1" type="$2" value="$3"
  # Use a temp file for anything that might trigger “param file” behavior or contains newlines
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
  echo "→ $name ($type)"
}

# --- import ---------------------------------------------------------------

while IFS='=' read -r k v; do
  # skip blanks and comments
  [[ -z "${k// }" || "$k" =~ ^# ]] && continue

  # strip surrounding quotes
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"

  # pick a default path
  path="$ROOT/app/$k"
  type="String"

  # classify
  if is_db "$k"; then
    # For DB_* we store under /db, but accept both DB_HOST and HOST, etc.
    base="$k"
    base="${base#DB_}"           # DB_HOST -> HOST
    path="$ROOT/db/$base"
  elif is_alert "$k"; then
    path="$ROOT/alert/$k"
    # Make obvious password-ish things SecureString
    [[ "$k" =~ (PASS|PASSWORD|SECRET|KEY)$ ]] && type="SecureString"
  else
    # app bucket: upgrade to SecureString for secrets looking keys
    [[ "$k" =~ (PASS|PASSWORD|SECRET|KEY)$ ]] && type="SecureString"
  fi

  # write
  put_param "$path" "$type" "$v"
done <"$ENVFILE"

echo "Done."

