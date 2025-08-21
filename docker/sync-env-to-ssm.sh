#!/usr/bin/env bash
set -euo pipefail

FILE="${1:?path to .env file}"
REGION="${2:?aws region}"
BASE="/bottrader/prod"

is_secret() {
  # mark obviously sensitive keys as secrets
  [[ "$1" =~ (PASSWORD|PASS|SECRET|KEY|TOKEN|PRIVATE|API_KEY) ]]
}

put_param() {
  local name="$1" value="$2" type="$3"
  aws ssm put-parameter \
    --region "$REGION" \
    --name "$name" \
    --type "$type" \
    --value "$value" \
    --overwrite >/dev/null
  echo "→ ${name} (${type})"
}

# read KEY=VALUE lines
while IFS= read -r line || [[ -n "$line" ]]; do
  # strip whitespace
  line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  # skip comments/blank
  [[ -z "$line" || "$line" =~ ^# ]] && continue
  # split on first =
  key="${line%%=*}"
  val="${line#*=}"

  # drop surrounding quotes if present
  val="${val%\"}"; val="${val#\"}"
  val="${val%\'}"; val="${val#\'}"

  if [[ "$key" =~ ^DB_(HOST|NAME|USER|PASSWORD|PORT)$ ]]; then
    # map DB_* -> lowercase names in /db
    base_lc="$(echo "${key#DB_}" | tr '[:upper:]' '[:lower:]')"
    path="${BASE}/db/${base_lc}"
    type="String"
    is_secret "$key" && type="SecureString"
    put_param "$path" "$val" "$type"
  else
    # everything else goes to /app as-is
    path="${BASE}/app/${key}"
    type="String"
    is_secret "$key" && type="SecureString"
    put_param "$path" "$val" "$type"
  fi
done < "$FILE"
