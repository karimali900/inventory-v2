#!/usr/bin/env bash
set -e

# ─── Fedora Prerequisites ───────────────────────────────────────
# Install gh CLI:
#   sudo dnf install gh
#
# Install Heroku CLI:
#   sudo dnf install heroku
#   or: curl https://cli-assets.heroku.com/install.sh | sh

# ─── Configuration ──────────────────────────────────────────────
REPO_NAME="inventory-v2"
HEROKU_APP_NAME="inventory-v2"  # change if taken

# ─── 1. Initialize Git ──────────────────────────────────────────
cd "$(dirname "$0")/.."

git init
git add -A
git commit -m "Initial commit: Inventory Management System"

# ─── 2. Create GitHub repo & push ──────────────────────────────
gh auth login
gh repo create "$REPO_NAME" --public --push --source=. --remote=origin

# ─── 3. Deploy to Heroku ─────────────────────────────────────────
heroku login
heroku create "$HEROKU_APP_NAME"

# Set environment variables
heroku config:set JWT_SECRET="$(openssl rand -hex 32)" -a "$HEROKU_APP_NAME"
heroku config:set ADMIN_USER=admin -a "$HEROKU_APP_NAME"
heroku config:set ADMIN_PASS=admin123 -a "$HEROKU_APP_NAME"

# Deploy
git push heroku main

# Open the app
heroku open -a "$HEROKU_APP_NAME"

echo ""
echo "✅ Done! App is running at: https://$HEROKU_APP_NAME.herokuapp.com"
