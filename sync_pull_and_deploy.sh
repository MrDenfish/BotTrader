#!/bin/bash

echo "� Cleaning all .DS_Store files..."
find . -name '.DS_Store' -delete

echo "� Checking for local changes..."
if [[ -n $(git status --porcelain) ]]; then
    echo "� Stashing uncommitted local changes..."
    git stash push -m "Auto-stash before sync from GitHub"
else
    echo "✅ No local changes to stash."
fi

echo "� Pulling latest changes from GitHub..."
git pull origin main

# Optional: Auto-pop stash if the stash was just created
if git stash list | grep -q "Auto-stash before sync from GitHub"; then
    echo "� Re-applying stashed changes..."
    git stash pop
fi

# Prompt user to run your DigitalOcean deploy script
read -p "� Run DigitalOcean deploy script now? (y/n): " yn
case $yn in
    [Yy]* )
        bash deploy_to_droplet.sh  # <-- rename your current script to this name
        ;;
    * )
        echo "✅ Sync complete. You can run deploy later with: bash deploy_to_droplet.sh"
        ;;
esac
