#!/usr/bin/env bash
# WebSocket health-check for raybags-chat.
#
# Connects to /ws/<random-id>, waits for the greeting message, then waits for
# the 25-second server heartbeat ping.  Exits 0 if both arrive in time; exits 1
# otherwise (network error, backend down, or heartbeat missing/too slow).
#
# Usage:
#   ./scripts/ws-healthcheck.sh [host]          # defaults to raybags.com
#   ./scripts/ws-healthcheck.sh localhost:8010  # local dev
#
# Dependencies: websocat (cargo install websocat), timeout, jq
set -euo pipefail

HOST="${1:-raybags.com}"
PROTO="wss"
[[ "$HOST" == localhost* ]] && PROTO="ws"

SID="hc-$(head -c 6 /dev/urandom | base64 | tr -dc 'a-z0-9' | head -c 8)"
URL="${PROTO}://${HOST}/ws/${SID}"

echo "[ws-healthcheck] connecting to $URL"

# websocat -1 prints messages one-per-line and exits after the first one.
# We need to wait for:
#   1. The greeting (type=msg, sender=agent)  — arrives within ~5s
#   2. The heartbeat ping (type=ping)         — arrives within ~30s
#
# We give it 35s total; if nothing comes, the connection must be dead/stale.

RECEIVED_GREETING=false
RECEIVED_PING=false
DEADLINE=$(( $(date +%s) + 35 ))

# Use process-substitution so we can break out of the while loop
while IFS= read -r line; do
    TYPE=$(echo "$line" | jq -r '.type // empty' 2>/dev/null || true)
    SENDER=$(echo "$line" | jq -r '.sender // empty' 2>/dev/null || true)

    if [[ "$TYPE" == "msg" && "$SENDER" == "agent" ]]; then
        echo "[ws-healthcheck] ✓ greeting received"
        RECEIVED_GREETING=true
    fi

    if [[ "$TYPE" == "ping" ]]; then
        echo "[ws-healthcheck] ✓ heartbeat ping received"
        RECEIVED_PING=true
    fi

    if $RECEIVED_GREETING && $RECEIVED_PING; then
        echo "[ws-healthcheck] PASS — connection alive and heartbeats working"
        exit 0
    fi

    if (( $(date +%s) >= DEADLINE )); then
        break
    fi
done < <(timeout 36 websocat --no-close -t "$URL" < /dev/null 2>/dev/null || true)

if ! $RECEIVED_GREETING; then
    echo "[ws-healthcheck] FAIL — no greeting received (backend may be down)"
    exit 1
fi
if ! $RECEIVED_PING; then
    echo "[ws-healthcheck] FAIL — no heartbeat ping received within 35s (keepalive may be broken)"
    exit 1
fi
