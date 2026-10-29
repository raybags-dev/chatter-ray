"""Unit tests for the admin AI-config endpoints and chat reset."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

ADMIN_Q = "?token=test-admin-token"


async def test_get_ai_config_returns_defaults(client):
    resp = await client.get(f"/api/v1/ai-config{ADMIN_Q}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "deepseek"
    assert body["has_api_key"] is False
    assert "deepseek" in body["providers"]
    assert "claude" in body["providers"]
    assert "gemini" in body["providers"]
    assert "groq" in body["providers"]


async def test_put_ai_config_switches_provider(client):
    resp = await client.put(
        f"/api/v1/ai-config{ADMIN_Q}",
        json={"provider": "claude", "model": "claude-sonnet-4-20250514", "api_key": "sk-ant-test-1234567890"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "claude"
    assert body["model"] == "claude-sonnet-4-20250514"
    assert body["has_api_key"] is True
    assert "…" in (body["api_key_preview"] or "")

    # GET reflects the change
    resp = await client.get(f"/api/v1/ai-config{ADMIN_Q}")
    assert resp.json()["provider"] == "claude"


async def test_put_ai_config_rejects_bad_provider(client):
    resp = await client.put(
        f"/api/v1/ai-config{ADMIN_Q}",
        json={"provider": "hal-9000"},
    )
    assert resp.status_code == 422


async def test_ai_config_requires_admin_token(client):
    # missing token -> FastAPI 422 (required query param); wrong token -> 403
    assert (await client.get("/api/v1/ai-config")).status_code == 422
    assert (await client.get("/api/v1/ai-config?token=wrong")).status_code == 403


async def test_ai_config_test_endpoint_success(client):
    fake_result = MagicMock()
    fake_result.content = "ok"
    fake_provider = MagicMock()
    fake_provider.chat = AsyncMock(return_value=fake_result)

    with patch(
        "app.api.v1.ai_config.build_provider", return_value=fake_provider
    ):
        resp = await client.post(
            f"/api/v1/ai-config/test{ADMIN_Q}",
            json={"provider": "deepseek", "model": None, "api_key": "sk-test"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["provider"] == "deepseek"


async def test_ai_config_test_endpoint_missing_key(client):
    with patch(
        "app.api.v1.ai_config.build_provider",
        side_effect=RuntimeError("No API key configured for provider 'gemini'"),
    ):
        resp = await client.post(
            f"/api/v1/ai-config/test{ADMIN_Q}",
            json={"provider": "gemini"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "No API key" in body["detail"]


async def test_chat_reset_closes_and_purges(client, db):
    from datetime import datetime, timedelta

    from sqlalchemy import select

    from app.models import ChatSession

    # seed: one active session + one old closed session
    db.add(ChatSession(session_id="active-1", status="active"))
    old = ChatSession(session_id="old-1", status="closed")
    old.updated_at = datetime.utcnow() - timedelta(days=30)
    db.add(old)
    await db.commit()

    resp = await client.post(f"/api/v1/sessions/reset{ADMIN_Q}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["closed_sessions"] >= 1
    assert body["purged_sessions"] >= 1

    remaining = (await db.scalars(select(ChatSession))).all()
    ids = {s.session_id: s.status for s in remaining}
    assert ids.get("active-1") == "closed"
    assert "old-1" not in ids


async def test_chat_reset_requires_admin(client):
    assert (await client.post("/api/v1/sessions/reset")).status_code == 422
    assert (await client.post("/api/v1/sessions/reset?token=wrong")).status_code == 403
