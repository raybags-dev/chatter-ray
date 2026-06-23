"""REST endpoints for the admin chat panel."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db
from app.models import ChatSession

router = APIRouter(prefix="/sessions", tags=["chat-sessions"])


def _require_admin(token: str = Query(...)):
    if token != settings.PORTFOLIO_ADMIN_TOKEN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


@router.get("", dependencies=[Depends(_require_admin)])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    status_filter: str | None = Query(None, alias="status"),
) -> list[dict]:
    q = select(ChatSession).order_by(ChatSession.id.desc()).limit(limit)
    if status_filter:
        q = q.where(ChatSession.status == status_filter)
    rows = (await db.scalars(q)).all()
    return [
        {
            "session_id": s.session_id,
            "visitor_name": s.visitor_name,
            "visitor_email": s.visitor_email,
            "status": s.status,
            "human_active": s.human_active,
            "created_at": s.created_at.isoformat(),
            "message_count": 0,
        }
        for s in rows
    ]


@router.get("/{session_id}", dependencies=[Depends(_require_admin)])
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    sess = await db.scalar(
        select(ChatSession)
        .where(ChatSession.session_id == session_id)
        .options(selectinload(ChatSession.messages))
    )
    if not sess:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return {
        "session_id": sess.session_id,
        "visitor_name": sess.visitor_name,
        "visitor_email": sess.visitor_email,
        "status": sess.status,
        "human_active": sess.human_active,
        "created_at": sess.created_at.isoformat(),
        "messages": [
            {
                "id": m.id,
                "sender": m.sender,
                "content": m.content,
                "tool_call": m.tool_call,
                "created_at": m.created_at.isoformat(),
            }
            for m in sess.messages
        ],
    }


@router.post("/{session_id}/takeover", dependencies=[Depends(_require_admin)])
async def takeover(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Flag this session as human-active so the LLM stops responding."""
    sess = await db.scalar(
        select(ChatSession).where(ChatSession.session_id == session_id)
    )
    if not sess:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    sess.human_active = True
    sess.status = "escalated"
    await db.commit()
    return {"ok": True}


@router.post("/{session_id}/release", dependencies=[Depends(_require_admin)])
async def release(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Hand session back to the LLM agent."""
    sess = await db.scalar(
        select(ChatSession).where(ChatSession.session_id == session_id)
    )
    if not sess:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    sess.human_active = False
    sess.status = "active"
    await db.commit()
    return {"ok": True}


@router.delete("/{session_id}/messages")
async def delete_visitor_messages(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Delete all messages for a session (visitor self-service — session_id is the secret)."""
    from sqlalchemy import delete as sql_delete
    from app.models import ChatMessage
    await db.execute(sql_delete(ChatMessage).where(ChatMessage.session_id == session_id))
    await db.commit()
    return {"ok": True}
