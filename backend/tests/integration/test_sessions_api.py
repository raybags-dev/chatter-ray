"""Integration tests for the /api/v1/sessions REST endpoints."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import ChatMessage, ChatSession
from tests.conftest import TEST_ADMIN_TOKEN


# ── auth ─────────────────────────────────────────────────────────────────────

async def test_list_sessions_requires_token(client):
    """Missing ?token query param → 422 (validation error)."""
    r = await client.get("/api/v1/sessions")
    assert r.status_code == 422


async def test_list_sessions_wrong_token(client):
    r = await client.get("/api/v1/sessions", params={"token": "wrong"})
    assert r.status_code == 403


async def test_get_session_wrong_token(client):
    r = await client.get("/api/v1/sessions/nonexistent", params={"token": "wrong"})
    assert r.status_code == 403


# ── list sessions ─────────────────────────────────────────────────────────────

async def test_list_sessions_empty(client):
    r = await client.get("/api/v1/sessions", params={"token": TEST_ADMIN_TOKEN})
    assert r.status_code == 200
    assert r.json() == []


async def test_list_sessions_returns_created_session(client, db):
    sess = ChatSession(session_id="list-test-1", ip_address="1.2.3.4", status="active")
    db.add(sess)
    await db.commit()

    r = await client.get("/api/v1/sessions", params={"token": TEST_ADMIN_TOKEN})
    assert r.status_code == 200
    items = r.json()
    assert any(s["session_id"] == "list-test-1" for s in items)


async def test_list_sessions_status_filter(client, db):
    db.add(ChatSession(session_id="filter-active", status="active"))
    db.add(ChatSession(session_id="filter-escalated", status="escalated"))
    await db.commit()

    r = await client.get(
        "/api/v1/sessions",
        params={"token": TEST_ADMIN_TOKEN, "status": "escalated"},
    )
    assert r.status_code == 200
    ids = [s["session_id"] for s in r.json()]
    assert "filter-escalated" in ids
    assert "filter-active" not in ids


# ── get session detail ────────────────────────────────────────────────────────

async def test_get_session_not_found(client):
    r = await client.get(
        "/api/v1/sessions/does-not-exist", params={"token": TEST_ADMIN_TOKEN}
    )
    assert r.status_code == 404


async def test_get_session_with_messages(client, db):
    sess = ChatSession(session_id="detail-test-1", status="active")
    db.add(sess)
    await db.flush()

    db.add(ChatMessage(session_id="detail-test-1", sender="user", content="Hello"))
    db.add(ChatMessage(session_id="detail-test-1", sender="agent", content="Hi there!"))
    await db.commit()

    r = await client.get(
        "/api/v1/sessions/detail-test-1", params={"token": TEST_ADMIN_TOKEN}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == "detail-test-1"
    assert len(body["messages"]) == 2
    assert body["messages"][0]["sender"] == "user"
    assert body["messages"][1]["sender"] == "agent"


# ── takeover / release ────────────────────────────────────────────────────────

async def test_takeover_sets_human_active(client, db):
    db.add(ChatSession(session_id="takeover-test-1", status="active", human_active=False))
    await db.commit()

    r = await client.post(
        "/api/v1/sessions/takeover-test-1/takeover", params={"token": TEST_ADMIN_TOKEN}
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    # Verify DB state
    sess = await db.scalar(
        select(ChatSession).where(ChatSession.session_id == "takeover-test-1")
    )
    assert sess is not None
    assert sess.human_active is True
    assert sess.status == "escalated"


async def test_release_clears_human_active(client, db):
    db.add(ChatSession(session_id="release-test-1", status="escalated", human_active=True))
    await db.commit()

    r = await client.post(
        "/api/v1/sessions/release-test-1/release", params={"token": TEST_ADMIN_TOKEN}
    )
    assert r.status_code == 200

    sess = await db.scalar(
        select(ChatSession).where(ChatSession.session_id == "release-test-1")
    )
    assert sess is not None
    assert sess.human_active is False
    assert sess.status == "active"


async def test_takeover_missing_session(client):
    r = await client.post(
        "/api/v1/sessions/ghost-session/takeover", params={"token": TEST_ADMIN_TOKEN}
    )
    assert r.status_code == 404


async def test_release_missing_session(client):
    r = await client.post(
        "/api/v1/sessions/ghost-session/release", params={"token": TEST_ADMIN_TOKEN}
    )
    assert r.status_code == 404


async def test_full_takeover_release_cycle(client, db):
    """Create session → takeover → verify → release → verify."""
    db.add(ChatSession(session_id="cycle-test-1", status="active", human_active=False))
    await db.commit()

    await client.post(
        "/api/v1/sessions/cycle-test-1/takeover", params={"token": TEST_ADMIN_TOKEN}
    )
    sess = await db.scalar(
        select(ChatSession).where(ChatSession.session_id == "cycle-test-1")
    )
    assert sess.human_active is True

    await client.post(
        "/api/v1/sessions/cycle-test-1/release", params={"token": TEST_ADMIN_TOKEN}
    )
    await db.refresh(sess)
    assert sess.human_active is False
    assert sess.status == "active"
