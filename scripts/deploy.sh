#!/usr/bin/env bash
# Push raybags-chat to GitHub.  CI/CD takes it from there.
# Usage: ./scripts/deploy.sh "commit message" [branch]
set -euo pipefail

COMMIT_MSG=${1:-"chore: update raybags-chat"}
BRANCH=${2:-main}
# Set WS_HEALTHCHECK=0 to skip the post-deploy health check.
WS_HEALTHCHECK="${WS_HEALTHCHECK:-1}"

GIT_USER=${GIT_USER:-raybags-dev}
GIT_EMAIL=${GIT_EMAIL:-baguma.github@gmail.com}

git config user.name "$GIT_USER"
git config user.email "$GIT_EMAIL"

git add -A
git commit -m "$COMMIT_MSG" || echo "Nothing new to commit"
git push origin "$BRANCH"

echo "Pushed to $BRANCH — GitHub Actions will build and deploy."

# Post-deploy WebSocket health check (auto-detects if websocat + jq are available).
if [[ "$WS_HEALTHCHECK" == "1" ]] && command -v websocat &>/dev/null && command -v jq &>/dev/null; then
    echo ""
    echo "Waiting 30s for deploy to settle before health-check..."
    sleep 30
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if bash "$SCRIPT_DIR/ws-healthcheck.sh" raybags.com; then
        echo "Health check passed — WebSocket keepalives confirmed."
    else
        echo "WARNING: Health check failed. Check the chat backend logs."
        echo "  Run manually: bash scripts/ws-healthcheck.sh raybags.com"
    fi
else
    echo "Tip: install websocat + jq to enable automatic post-deploy WS health-check."
fi
