"""Redis pub/sub helpers for routing messages across WebSocket connections.

Each chat session gets a channel key `chat:session:{session_id}`.
Admin connections subscribe to `chat:admin` to see all new messages.

Message envelope (JSON):
  {
    "type": "user_msg" | "agent_msg" | "human_msg" | "system",
    "session_id": str,
    "content": str,
    "sender": "user" | "agent" | "human",
    "ts": float,
  }
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings

_pool: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _pool


def session_channel(session_id: str) -> str:
    return f"chat:session:{session_id}"


ADMIN_CHANNEL = "chat:admin"


async def publish(channel: str, payload: dict[str, Any]) -> None:
    await get_redis().publish(channel, json.dumps(payload))


async def subscribe(*channels: str):
    """Return an async pubsub object subscribed to the given channels."""
    ps = get_redis().pubsub()
    await ps.subscribe(*channels)
    return ps
