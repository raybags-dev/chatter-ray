#!/bin/bash
set -e

# Decrypt core files if ENCRYPTION_KEY is set (handles encrypted-at-rest repos)
if [ -n "$ENCRYPTION_KEY" ]; then
    python scripts/decrypt_core.py
fi

# If custom command passed (e.g. alembic upgrade head), run it instead of uvicorn
if [ "$#" -gt 0 ]; then
    exec "$@"
else
    exec uvicorn app.main:app --host 0.0.0.0 --port 8010
fi
