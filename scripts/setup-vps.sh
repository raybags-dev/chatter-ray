#!/usr/bin/env bash
# One-time VPS bootstrap for raybags-chat.
# Subsequent deploys are handled automatically by GitHub Actions.
#
# Prerequisites on your local machine:
#   SSH key for the VPS must be in ~/.ssh/  (e.g., portfolio_base)
#   jq must be installed locally
#
# Usage: ./scripts/setup-vps.sh [vps-host] [vps-user]
set -euo pipefail

VPS_HOST=${1:-89.167.74.127}
VPS_USER=${2:-root}
SSH_KEY=${SSH_KEY:-~/.ssh/portfolio_base}
REMOTE_DIR="/opt/raybags-chat"

ssh_cmd() { ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "${VPS_USER}@${VPS_HOST}" "$@"; }

echo "==> Connecting to ${VPS_USER}@${VPS_HOST} ..."

# ── 1. Docker
ssh_cmd 'bash -s' <<'BOOTSTRAP'
set -e
if ! command -v docker &>/dev/null; then
  echo "Installing Docker..."
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
else
  echo "Docker already installed: $(docker --version)"
fi
BOOTSTRAP

# ── 2. Create service directory
ssh_cmd "mkdir -p ${REMOTE_DIR} && chmod 700 ${REMOTE_DIR}"
echo "==> ${REMOTE_DIR} created on VPS"

# ── 3. Pull the env file from GitHub Actions deploy (first run reminder)
echo ""
echo "==> NEXT STEPS:"
echo "    1. Add these GitHub Actions secrets in the chatter-ray repo:"
echo "       DATABASE_URL   — Supabase postgresql+asyncpg:// URL"
echo "       REDIS_URL      — redis://localhost:6379/1"
echo "       GROQ_API_KEY   — your Groq API key"
echo "       PORTFOLIO_ADMIN_TOKEN  — from portfolio-base .env"
echo "       APP_SECRET_KEY         — from portfolio-base .env"
echo "       DISCORD_WEBHOOK        — optional"
echo "       DOCKERHUB_USERNAME     — tonnybags"
echo "       DOCKERHUB_TOKEN        — your Docker Hub token"
echo "       VPS_HOST               — ${VPS_HOST}"
echo "       VPS_USER               — ${VPS_USER}"
echo "       VPS_SSH_KEY            — contents of your deploy private key"
echo ""
echo "    2. Push to main via: ./scripts/deploy.sh"
echo "       GitHub Actions builds images, SSHs to the VPS, writes .env.prod,"
echo "       runs alembic migrations, and brings services up."
echo ""
echo "==> VPS bootstrap complete."
