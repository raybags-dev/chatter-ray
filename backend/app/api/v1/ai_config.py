"""Admin REST endpoints for the chat AI provider selection.

GET    /ai-config        -> current provider/model (api key masked)
PUT    /ai-config        -> switch provider/model/api_key (runtime, no redeploy)
POST   /ai-config/test   -> verify provider + key with a tiny live call
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai_providers import DEFAULT_MODELS, PROVIDERS, build_provider
from app.core.config import settings
from app.core.database import get_db
from app.core.llm import invalidate_ai_config_cache
from app.models import ChatAIConfig

router = APIRouter(prefix="/ai-config", tags=["ai-config"])


async def _require_admin(token: str = Query(...)) -> None:
    if token == settings.PORTFOLIO_ADMIN_TOKEN:
        return
    if settings.PORTFOLIO_API_URL:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    f"{settings.PORTFOLIO_API_URL}/auth/me",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if r.status_code == 200:
                    data = r.json()
                    if data.get("is_superuser") or "admin" in str(data.get("roles", [])):
                        return
        except Exception:
            pass
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


class AIConfigUpdate(BaseModel):
    provider: str
    model: str | None = None
    api_key: str | None = None

    @field_validator("provider")
    @classmethod
    def _valid_provider(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in PROVIDERS:
            raise ValueError(f"provider must be one of: {', '.join(PROVIDERS)}")
        return v


def _serialize(cfg: ChatAIConfig) -> dict[str, Any]:
    key = cfg.api_key or ""
    return {
        "provider": cfg.provider,
        "model": cfg.model,
        "default_model": DEFAULT_MODELS.get(cfg.provider or "deepseek"),
        "has_api_key": bool(key),
        "api_key_preview": (
            (key[:6] + "…" + key[-4:]) if len(key) > 12 else ("<set>" if key else None)
        ),
        "providers": list(PROVIDERS),
        "default_models": dict(DEFAULT_MODELS),
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


async def _get_or_create(db: AsyncSession) -> ChatAIConfig:
    row = (await db.scalars(select(ChatAIConfig).where(ChatAIConfig.id == 1))).first()
    if row is None:
        row = ChatAIConfig(id=1, provider="deepseek")
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


@router.get("", dependencies=[Depends(_require_admin)])
async def get_ai_config(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return _serialize(await _get_or_create(db))


@router.put("", dependencies=[Depends(_require_admin)])
async def update_ai_config(
    payload: AIConfigUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    cfg = await _get_or_create(db)
    cfg.provider = payload.provider
    # Empty strings clear the override (fall back to provider default / env key)
    cfg.model = payload.model.strip() if payload.model and payload.model.strip() else None
    if payload.api_key is not None:
        cfg.api_key = payload.api_key.strip() or None
    await db.commit()
    await db.refresh(cfg)
    invalidate_ai_config_cache()
    return _serialize(cfg)


@router.post("/test", dependencies=[Depends(_require_admin)])
async def test_ai_config(
    payload: AIConfigUpdate,
) -> dict[str, Any]:
    """Live-check a provider/key without saving it."""
    try:
        provider = build_provider(
            payload.provider,
            payload.model,
            payload.api_key if payload.api_key else None,
        )
    except RuntimeError as exc:
        return {"ok": False, "detail": str(exc)}
    try:
        result = await provider.chat(
            [{"role": "user", "content": "Reply with exactly: ok"}]
        )
        return {
            "ok": True,
            "provider": payload.provider,
            "model": payload.model or DEFAULT_MODELS.get(payload.provider),
            "reply": (result.content or "")[:120],
        }
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:200]}"}
