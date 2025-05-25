#!/bin/bash

# ----------------------------------------
# Sync Cleanup and Git Pull → Commit → Push Script
# ----------------------------------------

echo "🧹 Cleaning system and Python cache files..."
find . -name '__pycache__' -type d -exec rm -r {} +
find . -name '.DS_Store' -delete
git reset .DS_Store 2>/dev/null
rm -f .DS_Store
echo "✅ Removed __pycache__ and .DS_Store files."

# Pull latest changes before committing
echo "📥 Pulling latest from GitHub (with rebase)..."
git pull --rebase origin main || {
    echo "❌ Pull failed — please resolve conflicts and try again."
    exit 1
}

# Stage .gitignore if modified
if [ -f .gitignore ]; then
    git add .gitignore
    echo "📄 Staged .gitignore"
fi

# Ask user if they want to commit all other changes
read -p "➕ Do you want to stage and commit ALL other changes too? (y/n): " yn
case $yn in
    [Yy]* )
        git add .
        git commit -m "Sync commit: code and cleanup"
        ;;
    [Nn]* )
        git commit -m "Update .gitignore only"
        ;;
    * )
        echo "❌ Please answer yes or no. Exiting."
        exit 1
        ;;
esac

# Push to GitHub
git push origin main
echo "🚀 Pushed to GitHub on branch 'main'"
