"""REST endpoints for the admin chat panel."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db
from app.core import redis as pubsub
from app.models import ChatSession, ChatMessage

router = APIRouter(prefix="/sessions", tags=["chat-sessions"])


async def _require_admin(token: str = Query(...)) -> None:
    if token == settings.PORTFOLIO_ADMIN_TOKEN:
        return
    # Also accept a valid portfolio admin JWT
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
    """Flag session as human-active, stop LLM, notify visitor Ray joined."""
    sess = await db.scalar(
        select(ChatSession).where(ChatSession.session_id == session_id)
    )
    if not sess:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    sess.human_active = True
    sess.status = "escalated"
    await db.commit()

    # Notify visitor that Ray has joined
    msg = {
        "type": "msg",
        "sender": "system",
        "content": "Oh good news — Raymond has joined the chat! I'll go right ahead and sign off now. It was really nice meeting you!",
        "session_id": session_id,
        "ts": time.time(),
    }
    await pubsub.publish(pubsub.session_channel(session_id), msg)
    return {"ok": True}


@router.post("/{session_id}/release", dependencies=[Depends(_require_admin)])
async def release(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Hand session back to the LLM agent, notify visitor."""
    sess = await db.scalar(
        select(ChatSession).where(ChatSession.session_id == session_id)
    )
    if not sess:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    sess.human_active = False
    sess.status = "active"
    await db.commit()

    # Notify visitor the AI is back
    msg = {
        "type": "msg",
        "sender": "system",
        "content": "Ray has stepped away — I'm back and happy to help with anything!",
        "session_id": session_id,
        "ts": time.time(),
    }
    await pubsub.publish(pubsub.session_channel(session_id), msg)
    return {"ok": True}


@router.delete("/{session_id}/messages")
async def delete_visitor_messages(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Delete all messages for a session (visitor self-service — session_id is the secret)."""
    from sqlalchemy import delete as sql_delete
    from app.models import ChatMessage
    await db.execute(sql_delete(ChatMessage).where(ChatMessage.session_id == session_id))
    await db.commit()
    return {"ok": True}
