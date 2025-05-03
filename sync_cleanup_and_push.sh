#!/bin/bash

# ----------------------------------------
# ✅ Purpose:
  #Run on laptop
  #
  #Cleans up system files and __pycache__
  #
  #Optionally commits all code changes
  #
  #Pushes changes to GitHub
# ----------------------------------------

echo "� Cleaning system and Python cache files..."

# Remove common untracked system and Python cache files
find . -name '__pycache__' -type d -exec rm -r {} +
find . -name '.DS_Store' -delete

echo "✅ Removed __pycache__ and .DS_Store files."

# Stage .gitignore (if modified)
if [ -f .gitignore ]; then
    git add .gitignore
    echo "� Staged .gitignore"
fi

# Ask if user wants to stage everything else
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
echo "� Pushed to GitHub on branch 'main'"
