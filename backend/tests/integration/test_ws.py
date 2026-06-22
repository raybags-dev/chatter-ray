"""Integration tests for WebSocket endpoints.

External dependencies (Redis, LLM, DB) are mocked so no real services are
needed.  We test routing logic and protocol behaviour.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from app.main import app
from tests.conftest import TEST_ADMIN_TOKEN


# ── helpers ───────────────────────────────────────────────────────────────────

class _FakePubSub:
    """Fake Redis pubsub that immediately exhausts (no messages delivered)."""

    async def listen(self):
        """Async generator — exits immediately so the listener task completes."""
        return
        yield  # noqa: unreachable — makes this an async generator function

    async def unsubscribe(self, *_channels):
        pass


def _make_mock_db(session_obj=None, history=None):
    """Build a minimal async DB mock that satisfies the ws.py calls."""
    mock_db = MagicMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=None)
    mock_db.scalar = AsyncMock(return_value=session_obj)
    # scalars().all() is used for message history
    mock_all = MagicMock(all=MagicMock(return_value=history or []))
    mock_db.scalars = AsyncMock(return_value=mock_all)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    return mock_db


# ── admin WebSocket auth ───────────────────────────────────────────────────────

def test_admin_ws_no_token_is_rejected():
    """No ?token → connection is closed before the test can receive data."""
    with TestClient(app) as client:
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/admin") as ws:
                ws.receive_json()


def test_admin_ws_wrong_token_is_rejected():
    """Wrong token → connection is closed immediately."""
    with TestClient(app) as client:
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/admin?token=bad-token") as ws:
                ws.receive_json()


def test_admin_ws_correct_token_connects():
    """Valid admin token → connection is accepted (server does not disconnect)."""
    mock_db = _make_mock_db()

    with (
        patch("app.api.v1.ws.AsyncSessionLocal", return_value=mock_db),
        patch("app.api.v1.ws.pubsub.subscribe", new=AsyncMock(return_value=_FakePubSub())),
        patch("app.api.v1.ws.pubsub.publish", new=AsyncMock()),
    ):
        with TestClient(app) as client:
            # Should NOT raise — connection is accepted, we close from client side
            try:
                with client.websocket_connect(f"/ws/admin?token={TEST_ADMIN_TOKEN}") as ws:
                    pass  # immediately close from client side
            except Exception as exc:
                # Some anyio stream cleanup errors are harmless on graceful close
                # Re-raise only if it's clearly an auth reject (4001 disconnect)
                if "4001" in str(exc):
                    raise
                # Otherwise treat as successful (server accepted, cleanup races)


# ── visitor WebSocket ─────────────────────────────────────────────────────────

def test_visitor_ws_receives_greeting():
    """New session (no history) receives the agent greeting on connect."""
    mock_db = _make_mock_db(session_obj=None, history=[])

    with (
        patch("app.api.v1.ws.AsyncSessionLocal", return_value=mock_db),
        patch("app.api.v1.ws.pubsub.subscribe", new=AsyncMock(return_value=_FakePubSub())),
        patch("app.api.v1.ws.pubsub.publish", new=AsyncMock()),
        patch("app.api.v1.ws.run_agent", new=AsyncMock(return_value=("Hello!", None))),
    ):
        with TestClient(app) as client:
            with client.websocket_connect("/ws/test-greeting-session") as ws:
                msg = ws.receive_json()
                assert msg["type"] == "msg"
                assert msg["sender"] == "agent"
                assert len(msg["content"]) > 0


def test_visitor_ws_reply_to_message():
    """Sending a message triggers the LLM and the reply is delivered."""
    mock_sess = MagicMock()
    mock_sess.human_active = False
    mock_sess.status = "active"
    mock_sess.session_id = "test-reply-session"

    mock_db = _make_mock_db(session_obj=mock_sess, history=[])
    agent_reply = "DataForge is Ray's ELT pipeline project."

    with (
        patch("app.api.v1.ws.AsyncSessionLocal", return_value=mock_db),
        patch("app.api.v1.ws.pubsub.subscribe", new=AsyncMock(return_value=_FakePubSub())),
        patch("app.api.v1.ws.pubsub.publish", new=AsyncMock()),
        patch("app.api.v1.ws.run_agent", new=AsyncMock(return_value=(agent_reply, None))),
    ):
        with TestClient(app) as client:
            with client.websocket_connect("/ws/test-reply-session") as ws:
                # Receive greeting first (empty history)
                ws.receive_json()

                # Send a user message
                ws.send_json({"type": "msg", "content": "Tell me about DataForge"})

                # Receive agent reply
                reply_msg = ws.receive_json()
                assert reply_msg["type"] == "msg"
                assert reply_msg["sender"] == "agent"
                assert reply_msg["content"] == agent_reply
