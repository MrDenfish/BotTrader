#!/bin/bash

# Detect system
USER=$(whoami)

# Use current directory as working dir
WORK_DIR="$(pwd)"
ENV_FILE="$WORK_DIR/.env_tradebot"
DROPLET_ALIAS="botdroplet"
DROPLET_PATH="/home/denfish/TradeBot"

echo "� Committing and pushing to GitHub..."
git add .
git commit -m "� Deploy update from $USER" || echo "ℹ️ Nothing to commit"
git push origin main

echo "� Uploading .env_tradebot to droplet..."
scp "$ENV_FILE" "$DROPLET_ALIAS:$DROPLET_PATH/.env_tradebot"

echo "� Connecting to droplet and restarting bot..."
ssh "$DROPLET_ALIAS" <<EOF
  set -e
  cd "$DROPLET_PATH"
  echo "� Pulling latest changes from GitHub..."
  git pull
  echo "� Restarting Docker Compose..."
  docker compose down --remove-orphans
  docker compose up -d --build
  echo "✅ Deployment complete."
EOF

echo "� All done!"

