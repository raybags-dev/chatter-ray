"""Unit tests for Redis pub/sub helpers."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.redis import ADMIN_CHANNEL, publish, session_channel


def test_session_channel_format():
    assert session_channel("abc123") == "chat:session:abc123"


def test_session_channel_different_ids():
    a = session_channel("session-1")
    b = session_channel("session-2")
    assert a != b
    assert a.startswith("chat:session:")
    assert b.startswith("chat:session:")


def test_admin_channel_constant():
    assert ADMIN_CHANNEL == "chat:admin"


async def test_publish_serializes_payload():
    mock_redis = MagicMock()
    mock_redis.publish = AsyncMock()

    payload = {"type": "msg", "sender": "user", "content": "hello", "session_id": "s1"}

    with patch("app.core.redis.get_redis", return_value=mock_redis):
        await publish("chat:session:s1", payload)

    mock_redis.publish.assert_awaited_once()
    call_args = mock_redis.publish.call_args
    channel_arg, data_arg = call_args[0]

    assert channel_arg == "chat:session:s1"
    decoded = json.loads(data_arg)
    assert decoded["sender"] == "user"
    assert decoded["content"] == "hello"


async def test_publish_arbitrary_channel():
    mock_redis = MagicMock()
    mock_redis.publish = AsyncMock()

    with patch("app.core.redis.get_redis", return_value=mock_redis):
        await publish(ADMIN_CHANNEL, {"type": "system", "content": "ping"})

    channel, _ = mock_redis.publish.call_args[0]
    assert channel == ADMIN_CHANNEL
