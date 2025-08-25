# ~/Python_Projects/BotTrader/docker/audit-ssm-vs-env.sh
#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 3 ]; then
  echo "usage: $0 <path-to-.env_tradebot> <aws-region> <env: dev|prod>"
  exit 1
fi

ENVFILE="$1"
REGION="$2"
STAGE="$3"
BASE="/bottrader/$STAGE"

if [ ! -f "$ENVFILE" ]; then
  echo "❌ env file not found: $ENVFILE" >&2; exit 1
fi

# map KEY -> SSM path
map_key() {
  k="$1"
  case "$k" in
    DB_*)              echo "$BASE/db/${k#DB_}";;
    DOCKER_DB_*)       echo "$BASE/docker/db/${k#DOCKER_DB_}";;
    ACCOUNT_PHONE|EMAIL|E_MAILPASS|MY_EMAIL|ALERT_* )
                        echo "$BASE/alert/$k";;
    *)                  echo "$BASE/app/$k";;
  esac
}

# read local env
declare -A LOCAL
while IFS='=' read -r k v; do
  [[ "$k" =~ ^[[:space:]]*# ]] && continue
  [ -z "$k" ] && continue
  # trim spaces
  k="$(printf '%s' "$k" | tr -d '[:space:]')"
  v="${v%%$'\r'}"
  # strip inline comments from numeric-ish and common params
  v="$(printf '%s' "$v" | sed 's/[[:space:]]*$//' )"
  [ -z "$k" ] && continue
  LOCAL["$k"]="$v"
done < "$ENVFILE"

# fetch remote SSM values
REMOTE_JSON="$(aws ssm get-parameters-by-path --path "$BASE" --with-decryption --recursive --region "$REGION" --output json)"
# build map path->value
declare -A REMOTE
while IFS=$'\t' read -r name value; do
  REMOTE["$name"]="$value"
done < <(jq -r '.Parameters[] | [.Name, .Value] | @tsv' <<<"$REMOTE_JSON")

# compare
missing=0; extra=0; diff=0
echo "🔎 Comparing desktop env to SSM at $BASE ..."
for k in "${!LOCAL[@]}"; do
  path="$(map_key "$k")"
  lv="${LOCAL[$k]}"
  rv="${REMOTE[$path]:-__MISSING__}"
  if [ "$rv" = "__MISSING__" ]; then
    echo "  + MISSING in SSM: $path   ← from $k"
    missing=$((missing+1))
  elif [ "$lv" != "$rv" ]; then
    mask="********"
    show_lv="$lv"; show_rv="$rv"
    case "$k" in *PASSWORD*|*SECRET*|*KEY*|*TOKEN*) show_lv="$mask"; show_rv="$mask";; esac
    echo "  ~ DIFF: $path"
    echo "      local:  $show_lv"
    echo "      remote: $show_rv"
    diff=$((diff+1))
  fi
done

# extras on SSM not in env
while IFS=$'\t' read -r name _; do
  key="${name##*/}"
  # reconstruct what desktop key would be and see if present
  present="no"
  for k in "${!LOCAL[@]}"; do
    if [ "$(map_key "$k")" = "$name" ]; then present="yes"; break; fi
  done
  if [ "$present" = "no" ]; then
    echo "  - EXTRA in SSM (not in .env): $name"
    extra=$((extra+1))
  fi
done < <(jq -r '.Parameters[] | [.Name, .Value] | @tsv' <<<"$REMOTE_JSON")

echo "== Summary: missing:$missing  diff:$diff  extra:$extra =="
[ $missing -eq 0 ] && [ $diff -eq 0 ] && echo "✅ SSM matches your .env"
