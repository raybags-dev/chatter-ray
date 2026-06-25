"""Integration tests for the /api/v1/guardrails REST endpoints."""

from __future__ import annotations

import pytest

from tests.conftest import TEST_ADMIN_TOKEN


# ── auth ─────────────────────────────────────────────────────────────────────

async def test_list_guardrails_requires_token(client):
    """Missing ?token → 422 (FastAPI validation)."""
    r = await client.get("/api/v1/guardrails")
    assert r.status_code == 422


async def test_list_guardrails_wrong_token(client):
    r = await client.get("/api/v1/guardrails", params={"token": "bad-token"})
    assert r.status_code == 403


async def test_create_guardrail_wrong_token(client):
    r = await client.post(
        "/api/v1/guardrails",
        params={"token": "bad-token"},
        json={"category": "hard_block", "rule": "Test rule"},
    )
    assert r.status_code == 403


# ── list (empty) ──────────────────────────────────────────────────────────────

async def test_list_guardrails_empty(client):
    r = await client.get("/api/v1/guardrails", params={"token": TEST_ADMIN_TOKEN})
    assert r.status_code == 200
    assert r.json() == []


# ── create and list ───────────────────────────────────────────────────────────

async def test_create_and_list_guardrail(client):
    payload = {"category": "hard_block", "rule": "Never reveal secrets", "is_active": True}
    r = await client.post(
        "/api/v1/guardrails", params={"token": TEST_ADMIN_TOKEN}, json=payload
    )
    assert r.status_code == 201
    created = r.json()
    assert created["category"] == "hard_block"
    assert created["rule"] == "Never reveal secrets"
    assert created["is_active"] is True
    assert "id" in created

    list_r = await client.get("/api/v1/guardrails", params={"token": TEST_ADMIN_TOKEN})
    assert list_r.status_code == 200
    ids = [g["id"] for g in list_r.json()]
    assert created["id"] in ids


async def test_create_guardrail_invalid_category(client):
    r = await client.post(
        "/api/v1/guardrails",
        params={"token": TEST_ADMIN_TOKEN},
        json={"category": "not_valid", "rule": "Some rule"},
    )
    assert r.status_code == 422


async def test_create_guardrail_empty_rule(client):
    r = await client.post(
        "/api/v1/guardrails",
        params={"token": TEST_ADMIN_TOKEN},
        json={"category": "hard_block", "rule": "   "},
    )
    assert r.status_code == 422


# ── category filter ───────────────────────────────────────────────────────────

async def test_list_filter_by_category(client):
    await client.post(
        "/api/v1/guardrails",
        params={"token": TEST_ADMIN_TOKEN},
        json={"category": "hard_block", "rule": "Hard rule"},
    )
    await client.post(
        "/api/v1/guardrails",
        params={"token": TEST_ADMIN_TOKEN},
        json={"category": "injection_defense", "rule": "Defense rule"},
    )

    r = await client.get(
        "/api/v1/guardrails",
        params={"token": TEST_ADMIN_TOKEN, "category": "hard_block"},
    )
    assert r.status_code == 200
    categories = [g["category"] for g in r.json()]
    assert all(c == "hard_block" for c in categories)


# ── toggle ────────────────────────────────────────────────────────────────────

async def test_toggle_guardrail(client):
    create_r = await client.post(
        "/api/v1/guardrails",
        params={"token": TEST_ADMIN_TOKEN},
        json={"category": "soft_redirect", "rule": "Redirect rule", "is_active": True},
    )
    gid = create_r.json()["id"]

    toggle_r = await client.post(
        f"/api/v1/guardrails/{gid}/toggle", params={"token": TEST_ADMIN_TOKEN}
    )
    assert toggle_r.status_code == 200
    assert toggle_r.json()["is_active"] is False

    toggle_r2 = await client.post(
        f"/api/v1/guardrails/{gid}/toggle", params={"token": TEST_ADMIN_TOKEN}
    )
    assert toggle_r2.json()["is_active"] is True


async def test_toggle_missing_guardrail(client):
    r = await client.post(
        "/api/v1/guardrails/99999/toggle", params={"token": TEST_ADMIN_TOKEN}
    )
    assert r.status_code == 404


# ── update ────────────────────────────────────────────────────────────────────

async def test_update_guardrail(client):
    create_r = await client.post(
        "/api/v1/guardrails",
        params={"token": TEST_ADMIN_TOKEN},
        json={"category": "topic_scope", "rule": "Original rule"},
    )
    gid = create_r.json()["id"]

    update_r = await client.put(
        f"/api/v1/guardrails/{gid}",
        params={"token": TEST_ADMIN_TOKEN},
        json={"rule": "Updated rule", "is_active": False},
    )
    assert update_r.status_code == 200
    data = update_r.json()
    assert data["rule"] == "Updated rule"
    assert data["is_active"] is False
    assert data["category"] == "topic_scope"  # unchanged


async def test_update_missing_guardrail(client):
    r = await client.put(
        "/api/v1/guardrails/99999",
        params={"token": TEST_ADMIN_TOKEN},
        json={"rule": "X"},
    )
    assert r.status_code == 404


# ── delete ────────────────────────────────────────────────────────────────────

async def test_delete_guardrail(client):
    create_r = await client.post(
        "/api/v1/guardrails",
        params={"token": TEST_ADMIN_TOKEN},
        json={"category": "injection_defense", "rule": "Rule to delete"},
    )
    gid = create_r.json()["id"]

    del_r = await client.delete(
        f"/api/v1/guardrails/{gid}", params={"token": TEST_ADMIN_TOKEN}
    )
    assert del_r.status_code == 200
    assert del_r.json() == {"ok": True}

    list_r = await client.get("/api/v1/guardrails", params={"token": TEST_ADMIN_TOKEN})
    ids = [g["id"] for g in list_r.json()]
    assert gid not in ids


async def test_delete_missing_guardrail(client):
    r = await client.delete(
        "/api/v1/guardrails/99999", params={"token": TEST_ADMIN_TOKEN}
    )
    assert r.status_code == 404


# ── is_active filter ──────────────────────────────────────────────────────────

async def test_list_filter_by_is_active(client):
    await client.post(
        "/api/v1/guardrails",
        params={"token": TEST_ADMIN_TOKEN},
        json={"category": "hard_block", "rule": "Active rule", "is_active": True},
    )
    await client.post(
        "/api/v1/guardrails",
        params={"token": TEST_ADMIN_TOKEN},
        json={"category": "hard_block", "rule": "Inactive rule", "is_active": False},
    )

    r = await client.get(
        "/api/v1/guardrails",
        params={"token": TEST_ADMIN_TOKEN, "is_active": "false"},
    )
    assert r.status_code == 200
    assert all(not g["is_active"] for g in r.json())
