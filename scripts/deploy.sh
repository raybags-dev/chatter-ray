#!/usr/bin/env bash
# Push raybags-chat to GitHub.  CI/CD takes it from there.
# Usage: ./scripts/deploy.sh "commit message" [branch]
set -euo pipefail

COMMIT_MSG=${1:-"chore: update raybags-chat"}
BRANCH=${2:-main}

GIT_USER=${GIT_USER:-raybags-dev}
GIT_EMAIL=${GIT_EMAIL:-baguma.github@gmail.com}

git config user.name "$GIT_USER"
git config user.email "$GIT_EMAIL"

git add -A
git commit -m "$COMMIT_MSG" || echo "Nothing new to commit"
git push origin "$BRANCH"

echo "Pushed to $BRANCH — GitHub Actions will build and deploy."
