"""Shared pytest fixtures.

Sets test environment variables BEFORE any app module imports so that
pydantic-settings picks them up when the Settings singleton is first created.
"""

from __future__ import annotations

import os

# ── must come before any `from app import ...` ──────────────────────────────
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PORTFOLIO_ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("SECRET_KEY", "test-secret-key-00000000000000000000000000000000")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
# ────────────────────────────────────────────────────────────────────────────

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

# Force fresh settings read with our env vars.
from app.core.config import get_settings

get_settings.cache_clear()

from app.core.database import Base, get_db  # noqa: E402 — must come after env setup
from app.main import app  # noqa: E402

TEST_ADMIN_TOKEN = "test-admin-token"
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="session")
async def engine():
    """Single in-memory SQLite engine shared for the whole test session."""
    eng = create_async_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine):
    """Per-test async DB session; rolls back after each test."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(db):
    """Async HTTP test client with the DB dependency overridden."""

    async def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
