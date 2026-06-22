"""Integration test — health endpoint."""

from __future__ import annotations


async def test_health_returns_200(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
