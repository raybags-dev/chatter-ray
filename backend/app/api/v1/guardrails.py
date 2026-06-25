"""Admin REST endpoints for managing LLM guardrails."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.llm import invalidate_guardrails_cache
from app.models import ChatGuardrail

router = APIRouter(prefix="/guardrails", tags=["guardrails"])

_VALID_CATEGORIES = {"hard_block", "soft_redirect", "topic_scope", "injection_defense"}


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


def _serialize(g: ChatGuardrail) -> dict[str, Any]:
    return {
        "id": g.id,
        "category": g.category,
        "rule": g.rule,
        "is_active": g.is_active,
        "created_at": (
            g.created_at.isoformat() if isinstance(g.created_at, datetime) else g.created_at
        ),
        "updated_at": (
            g.updated_at.isoformat() if isinstance(g.updated_at, datetime) else g.updated_at
        ),
    }


class GuardrailCreate(BaseModel):
    category: str
    rule: str
    is_active: bool = True

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        if v not in _VALID_CATEGORIES:
            raise ValueError(f"category must be one of {sorted(_VALID_CATEGORIES)}")
        return v

    @field_validator("rule")
    @classmethod
    def validate_rule(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("rule cannot be empty")
        return v


class GuardrailUpdate(BaseModel):
    category: str | None = None
    rule: str | None = None
    is_active: bool | None = None

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_CATEGORIES:
            raise ValueError(f"category must be one of {sorted(_VALID_CATEGORIES)}")
        return v

    @field_validator("rule")
    @classmethod
    def validate_rule(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("rule cannot be empty")
        return v


@router.get("", dependencies=[Depends(_require_admin)])
async def list_guardrails(
    db: AsyncSession = Depends(get_db),
    category: str | None = Query(None),
    is_active: bool | None = Query(None),
) -> list[dict]:
    q = select(ChatGuardrail).order_by(ChatGuardrail.category, ChatGuardrail.id)
    if category is not None:
        q = q.where(ChatGuardrail.category == category)
    if is_active is not None:
        q = q.where(ChatGuardrail.is_active.is_(is_active))
    rows = (await db.scalars(q)).all()
    return [_serialize(g) for g in rows]


@router.post("", dependencies=[Depends(_require_admin)], status_code=status.HTTP_201_CREATED)
async def create_guardrail(
    body: GuardrailCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    g = ChatGuardrail(category=body.category, rule=body.rule, is_active=body.is_active)
    db.add(g)
    await db.commit()
    await db.refresh(g)
    invalidate_guardrails_cache()
    return _serialize(g)


@router.put("/{guardrail_id}", dependencies=[Depends(_require_admin)])
async def update_guardrail(
    guardrail_id: int,
    body: GuardrailUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    g = await db.scalar(select(ChatGuardrail).where(ChatGuardrail.id == guardrail_id))
    if not g:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Guardrail not found")
    if body.category is not None:
        g.category = body.category
    if body.rule is not None:
        g.rule = body.rule
    if body.is_active is not None:
        g.is_active = body.is_active
    g.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(g)
    invalidate_guardrails_cache()
    return _serialize(g)


@router.delete("/{guardrail_id}", dependencies=[Depends(_require_admin)])
async def delete_guardrail(
    guardrail_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    g = await db.scalar(select(ChatGuardrail).where(ChatGuardrail.id == guardrail_id))
    if not g:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Guardrail not found")
    await db.delete(g)
    await db.commit()
    invalidate_guardrails_cache()
    return {"ok": True}


@router.post("/{guardrail_id}/toggle", dependencies=[Depends(_require_admin)])
async def toggle_guardrail(
    guardrail_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    g = await db.scalar(select(ChatGuardrail).where(ChatGuardrail.id == guardrail_id))
    if not g:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Guardrail not found")
    g.is_active = not g.is_active
    g.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(g)
    invalidate_guardrails_cache()
    return _serialize(g)
