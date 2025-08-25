#!/usr/bin/env bash
set -euo pipefail

# --- config (edit once) ---
ENVFILE="${1:-../.env_tradebot}"   # path to your desktop .env
REGION="${2:-us-west-2}"
STAGE="${3:-prod}"
SSH_HOST="${4:-ubuntu@54.187.252.72}"   # ubuntu@<EC2_PUBLIC_IP>
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMPORT="${REPO_ROOT}/docker/import-env-to-ssm.sh"

# --- sanity ---
[ -f "$ENVFILE" ] || { echo "❌ env file not found: $ENVFILE"; exit 1; }
command -v aws >/dev/null || { echo "❌ aws cli required"; exit 1; }
[ -x "$IMPORT" ] || chmod +x "$IMPORT"

echo "🧹 Clean local junk..."
find "$REPO_ROOT" -name '.DS_Store' -delete
find "$REPO_ROOT" -name '__pycache__' -type d -exec rm -rf {} +

echo "📦 Commit & push..."
git -C "$REPO_ROOT" add -A
git -C "$REPO_ROOT" commit -m "🚀 Deploy update" || echo "ℹ️ Nothing to commit"
git -C "$REPO_ROOT" push origin main

echo "🔁 Sync config to SSM: /bottrader/$STAGE ..."
"$IMPORT" "$ENVFILE" "$REGION" "$STAGE" --force

echo "🚀 Remote update via SSH on $SSH_HOST ..."
ssh -o StrictHostKeyChecking=no "$SSH_HOST" 'cd /opt/bot && ./update.sh'

echo "✅ Done."
