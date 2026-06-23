"""REST endpoints for the admin chat panel."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db, AsyncSessionLocal
from app.core import redis as pubsub
from app.models import ChatSession, ChatMessage

router = APIRouter(prefix="/sessions", tags=["chat-sessions"])


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


async def _send_handoff_sequence(session_id: str) -> None:
    """LLM shows typing then sends farewell before stepping aside for admin."""
    typing_evt = {
        "type": "typing",
        "sender": "agent",
        "session_id": session_id,
        "ts": time.time(),
    }
    await pubsub.publish(pubsub.session_channel(session_id), typing_evt)
    await asyncio.sleep(1.8)
    farewell = (
        "Great news — Raymond just picked up this session and will take it from here. "
        "It's been a pleasure chatting with you! For now, this is goodbye from me. \U0001f44b"
    )
    msg = {
        "type": "msg",
        "sender": "agent",
        "content": farewell,
        "session_id": session_id,
        "ts": time.time(),
    }
    async with AsyncSessionLocal() as db:
        db.add(ChatMessage(session_id=session_id, sender="agent", content=farewell))
        await db.commit()
    await pubsub.publish(pubsub.session_channel(session_id), msg)
    await pubsub.publish(pubsub.ADMIN_CHANNEL, msg)


async def _send_release_sequence(session_id: str) -> None:
    """LLM announces it's back after admin releases the session."""
    typing_evt = {
        "type": "typing",
        "sender": "agent",
        "session_id": session_id,
        "ts": time.time(),
    }
    await pubsub.publish(pubsub.session_channel(session_id), typing_evt)
    await asyncio.sleep(1.8)
    text = (
        "Looks like Raymond isn't available at the moment — I'm back and happy to pick up "
        "right where we left off. What can I help you with?"
    )
    msg = {
        "type": "msg",
        "sender": "agent",
        "content": text,
        "session_id": session_id,
        "ts": time.time(),
    }
    async with AsyncSessionLocal() as db:
        db.add(ChatMessage(session_id=session_id, sender="agent", content=text))
        await db.commit()
    await pubsub.publish(pubsub.session_channel(session_id), msg)
    await pubsub.publish(pubsub.ADMIN_CHANNEL, msg)


@router.post("/{session_id}/takeover", dependencies=[Depends(_require_admin)])
async def takeover(
    session_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Flag session as human-active; LLM sends a farewell in the background."""
    sess = await db.scalar(
        select(ChatSession).where(ChatSession.session_id == session_id)
    )
    if not sess:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    sess.human_active = True
    sess.status = "escalated"
    await db.commit()
    background_tasks.add_task(_send_handoff_sequence, session_id)
    return {"ok": True}


@router.post("/{session_id}/release", dependencies=[Depends(_require_admin)])
async def release(
    session_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Hand session back to the LLM agent."""
    sess = await db.scalar(
        select(ChatSession).where(ChatSession.session_id == session_id)
    )
    if not sess:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    sess.human_active = False
    sess.status = "active"
    await db.commit()
    background_tasks.add_task(_send_release_sequence, session_id)
    return {"ok": True}


@router.post("/{session_id}/close", dependencies=[Depends(_require_admin)])
async def close_session(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Mark a session as closed (soft-delete). Preserves message history."""
    sess = await db.scalar(
        select(ChatSession).where(ChatSession.session_id == session_id)
    )
    if not sess:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    sess.status = "closed"
    sess.human_active = False
    await db.commit()
    return {"ok": True}


@router.delete("/{session_id}", dependencies=[Depends(_require_admin)])
async def delete_session(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Permanently delete a session and all its messages."""
    sess = await db.scalar(
        select(ChatSession).where(ChatSession.session_id == session_id)
    )
    if not sess:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    await db.delete(sess)
    await db.commit()
    return {"ok": True}


@router.delete("/{session_id}/messages")
async def delete_visitor_messages(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Delete all messages for a session (visitor self-service — session_id is the secret)."""
    from sqlalchemy import delete as sql_delete
    await db.execute(sql_delete(ChatMessage).where(ChatMessage.session_id == session_id))
    await db.commit()
    return {"ok": True}


@router.post("/cleanup", dependencies=[Depends(_require_admin)])
async def cleanup_stale(db: AsyncSession = Depends(get_db)) -> dict:
    """Close sessions inactive for >30 min (non-escalated only).

    Returns the number of sessions closed.
    """
    from datetime import datetime, timedelta
    from sqlalchemy import and_, update as sql_update

    cutoff = datetime.utcnow() - timedelta(minutes=30)

    last_msg_sq = (
        select(
            ChatMessage.session_id,
            func.max(ChatMessage.created_at).label("last_msg_at"),
        )
        .group_by(ChatMessage.session_id)
        .subquery()
    )

    stale_q = (
        select(ChatSession.session_id)
        .outerjoin(last_msg_sq, ChatSession.session_id == last_msg_sq.c.session_id)
        .where(
            and_(
                ChatSession.status.in_(["active", "idle"]),
                (last_msg_sq.c.last_msg_at < cutoff)
                | (last_msg_sq.c.last_msg_at == None),  # noqa: E711
            )
        )
    )
    stale_ids = list((await db.scalars(stale_q)).all())

    if stale_ids:
        await db.execute(
            sql_update(ChatSession)
            .where(ChatSession.session_id.in_(stale_ids))
            .values(status="closed", human_active=False)
        )
        await db.commit()

    return {"ok": True, "closed": len(stale_ids)}
