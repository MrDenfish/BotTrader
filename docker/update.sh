# /opt/bot/update.sh
#!/usr/bin/env bash
set -euo pipefail
cd /opt/bot

# guard against conflict markers in any env files you might still use
if [ -f .env_tradebot ] && grep -qE '^(<<<<<<<|=======|>>>>>>>)' .env_tradebot; then
  echo "❌ .env_tradebot contains merge-conflict markers. Resolve before deploy."
  exit 1
fi

echo "🔄 Updating repo..."
git fetch origin
git reset --hard origin/main

echo "🐳 Building/pulling images..."
docker compose build --pull

echo "🚦 Restarting stack..."
docker compose down
docker compose up -d

echo "✅ Running containers:"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Image}}"