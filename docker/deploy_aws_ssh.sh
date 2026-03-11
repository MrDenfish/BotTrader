#!/usr/bin/env bash
set -euo pipefail

# --- config (edit once) ---
ENVFILE="${1:-../.env}"   # path to your unified .env
REGION="${2:-us-west-2}"
STAGE="${3:-prod}"
SSH_HOST="${4:-ubuntu@44.238.14.228}"   # ubuntu@<EC2_PUBLIC_IP>
SSH_KEY="${SSH_KEY:-$HOME/.ssh/bottrader-key.pem}"     # ✅ added (path to your .pem)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMPORT="${REPO_ROOT}/docker/import-env-to-ssm.sh"

# Use existing AWS_PROFILE if set; else fall back to the SSO profile you actually have
AWS_PROFILE="${AWS_PROFILE:-BotTrader-SSM-Admin-942165495776}"
export AWS_PROFILE

# --- sanity ---
[ -f "$ENVFILE" ] || { echo "❌ env file not found: $ENVFILE"; exit 1; }
command -v aws >/dev/null || { echo "❌ aws cli required"; exit 1; }
[ -x "$IMPORT" ] || chmod +x "$IMPORT"

# ✅ AWS preflight (nice UX if you forgot to `aws sso login`)
aws sts get-caller-identity >/dev/null 2>&1 || {
  echo "❌ AWS CLI not authenticated. Run: aws sso login --profile $AWS_PROFILE"; exit 1;
}

echo "🧹 Clean local junk..."
find "$REPO_ROOT" -name '.DS_Store' -delete
find "$REPO_ROOT" -name '__pycache__' -type d -exec rm -rf {} +

echo "📦 Commit & push..."
git -C "$REPO_ROOT" add -A
git -C "$REPO_ROOT" commit -m "🚀 Deploy update" || echo "ℹ️ Nothing to commit"
git -C "$REPO_ROOT" push origin main

echo "🔁 Sync config to SSM: /bottrader/$STAGE ..."
"$IMPORT" "$ENVFILE" "$REGION" "$STAGE" --force

# ✅ quick SSH preflight with the key you intend to use
echo "🔐 Testing SSH connectivity..."
ssh -o StrictHostKeyChecking=no -i "$SSH_KEY" "$SSH_HOST" "echo ok" >/dev/null

echo "🚀 Remote update via SSH on $SSH_HOST ..."
ssh -o StrictHostKeyChecking=no -i "$SSH_KEY" "$SSH_HOST" 'cd /opt/bot && ./update.sh'

echo "✅ Done."
