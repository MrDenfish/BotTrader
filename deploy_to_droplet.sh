#!/bin/bash

# Define paths
DEV_DIR="$HOME/Library/CloudStorage/Dropbox/Coins/BotTraderv3.0"
PROD_DIR="$HOME/Library/CloudStorage/Dropbox/Coins/Bot_Droplet"
DROPLET_ALIAS="botdroplet"  # from your ~/.ssh/config
DROPLET_PATH="/home/denfish/TradeBot"
ENV_FILE="$DEV_DIR/.env_tradebot"

echo "🔰 Syncing changes from development to production directory..."
rsync -av --exclude='.git' --exclude='__pycache__' --exclude='logs' \
  "$DEV_DIR/" "$PROD_DIR/"

cd "$PROD_DIR" || exit

echo "🔰 Committing and pushing to GitHub..."
git add .
git commit -m "🔰 Deploy update from BotTraderv3.0 to Bot_Droplet" || echo "ℹ️ Nothing to commit"
git push origin main

echo "🔰 Uploading .env_tradebot to droplet..."
scp "$ENV_FILE" "$DROPLET_ALIAS:$DROPLET_PATH/.env_tradebot"

echo "♻️ Connecting to droplet and restarting bot..."
ssh "$DROPLET_ALIAS" <<EOF
  set -e
  cd "$DROPLET_PATH"
  echo "♻️ Pulling latest changes from GitHub..."
  git pull
  echo "♻️ Restarting Docker Compose..."
  docker compose down --remove-orphans
  docker compose up -d --build
  echo "✅ Deployment complete."
EOF

echo "� All done!"
