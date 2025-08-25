# ~/Python_Projects/BotTrader/docker/sync-config.sh
#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 3 ]; then
  echo "usage: $0 <path-to-.env_tradebot> <aws-region> <env: dev|prod> [--deploy <user@host>]"
  exit 1
fi

ENVFILE="$1"; REGION="$2"; STAGE="$3"; shift 3
DEPLOY_HOST=""

if [ "${1:-}" = "--deploy" ]; then
  DEPLOY_HOST="${2:-}"; shift 2
  [ -z "$DEPLOY_HOST" ] && { echo "❌ provide --deploy <user@host>"; exit 1; }
fi

# push to SSM
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$SCRIPT_DIR/import-env-to-ssm.sh" "$ENVFILE" "$REGION" "$STAGE" --force

# optional deploy
if [ -n "$DEPLOY_HOST" ]; then
  echo "🚀 Deploying to $DEPLOY_HOST ..."
  ssh "$DEPLOY_HOST" 'cd /opt/bot && ./update.sh'
fi
echo "✅ Done."
