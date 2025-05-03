#!/bin/bash

# Detect machine type
USER=$(whoami)
IS_LAPTOP=false
IS_DESKTOP=false

if [[ "$USER" == "jack" ]]; then
    IS_LAPTOP=true
elif [[ "$USER" == "Manny" ]]; then
    IS_DESKTOP=true
fi

echo "� Cleaning .DS_Store and __pycache__..."
find . -name '.DS_Store' -delete
find . -name '__pycache__' -type d -exec rm -r {} +

# Prompt: Run all scripts in sequence?
read -p "� Run full sync → pull → deploy sequence? (y/n): " run_all
if [[ $run_all == [Yy]* ]]; then
    echo "� Running all three scripts..."

    if [[ -f sync_cleanup_and_push.sh ]]; then
        bash sync_cleanup_and_push.sh
    else
        echo "⚠️ sync_cleanup_and_push.sh not found."
    fi

    if [[ -f sync_pull_and_deploy.sh ]]; then
        bash sync_pull_and_deploy.sh
    else
        echo "⚠️ sync_pull_and_deploy.sh not found."
    fi

    exit 0
fi

# If not running all, proceed based on machine
if $IS_LAPTOP; then
    echo "� Detected LAPTOP environment."

    read -p "➕ Stage and commit ALL changes before pushing to GitHub? (y/n): " yn
    if [[ $yn == [Yy]* ]]; then
        git add .
        git commit -m "Sync commit from laptop"
    else
        echo "� Skipping commit."
    fi

    echo "� Pushing to GitHub..."
    git push origin main

elif $IS_DESKTOP; then
    echo "�️ Detected DESKTOP environment."

    echo "� Checking for local changes..."
    if [[ -n $(git status --porcelain) ]]; then
        echo "� Stashing local changes..."
        git stash push -m "Auto-stash before sync from GitHub"
    fi

    echo "� Pulling latest from GitHub..."
    git pull origin main

    if git stash list | grep -q "Auto-stash before sync from GitHub"; then
        echo "� Reapplying stashed changes..."
        git stash pop
    fi

    read -p "� Run DigitalOcean deploy script now? (y/n): " deploy
    if [[ $deploy == [Yy]* ]]; then
        bash deploy_to_droplet.sh
    else
        echo "✅ Pull complete. Deploy skipped."
    fi
else
    echo "⚠️ Unknown user/machine. Please customize USER detection logic."
fi
