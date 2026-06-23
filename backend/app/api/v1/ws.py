"""WebSocket endpoint — one connection per visitor session.

Flow:
  1. Client connects to /ws/{session_id}
  2. Server creates or resumes ChatSession in DB
  3. Server subscribes to Redis channel `chat:session:{session_id}`
  4. Client sends JSON: {"type": "msg", "content": "hello"}
  5. Server:
     a. Saves message to DB
     b. Publishes to Redis admin channel (admin panel sees it live)
     c. If session.human_active: waits for human reply via Redis
        Else: runs LLM agent, streams reply back
  6. Admin connects to /ws/admin?token=<jwt> and subscribes to all sessions

Message format sent to client:
  {"type": "msg", "sender": "user"|"agent"|"human"|"system", "content": "...", "ts": 1234}
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.llm import run_agent
from app.core import redis as pubsub
from app.models import ChatMessage, ChatSession

router = APIRouter()


def _now() -> float:
    return time.time()


def _envelope(sender: str, content: str, session_id: str, **extra: Any) -> dict:
    return {"type": "msg", "sender": sender, "content": content,
            "session_id": session_id, "ts": _now(), **extra}


async def _get_or_create_session(db: AsyncSession, session_id: str, ip: str) -> ChatSession:
    sess = await db.scalar(
        select(ChatSession).where(ChatSession.session_id == session_id)
    )
    if not sess:
        sess = ChatSession(session_id=session_id, ip_address=ip, status="active")
        db.add(sess)
        await db.commit()
        await db.refresh(sess)
    return sess


async def _history(db: AsyncSession, session_id: str) -> list[dict[str, str]]:
    msgs = (
        await db.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id)
            .limit(40)
        )
    ).all()
    return [{"role": "user" if m.sender == "user" else "assistant", "content": m.content}
            for m in msgs]


async def _save(db: AsyncSession, session_id: str, sender: str,
                content: str, tool_call: str | None = None) -> None:
    db.add(ChatMessage(session_id=session_id, sender=sender,
                       content=content, tool_call=tool_call))
    await db.commit()


@router.websocket("/ws/{session_id}")
async def visitor_ws(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    ip = websocket.client.host if websocket.client else "unknown"

    async with AsyncSessionLocal() as db:
        session = await _get_or_create_session(db, session_id, ip)

    # Subscribe to this session's Redis channel (for human replies)
    ps = await pubsub.subscribe(pubsub.session_channel(session_id))

    async def _redis_listener() -> None:
        """Forward Redis messages (human replies) to the visitor's WebSocket."""
        async for raw in ps.listen():
            if raw["type"] != "message":
                continue
            try:
                payload = json.loads(raw["data"])
            except Exception:
                continue
            if payload.get("sender") in ("human", "system"):
                await websocket.send_json(payload)

    redis_task = asyncio.create_task(_redis_listener())

    try:
        # Greet new sessions — check DB history; save greeting so reconnects don't re-greet
        async with AsyncSessionLocal() as db:
            history = await _history(db, session_id)

        if not history:
            greeting_text = "Hi! I'm Raymond's AI assistant. Welcome to his portfolio — if you have any inquiries, requests, or questions, feel free to ask me."
            greeting = _envelope("agent", greeting_text, session_id)
            await websocket.send_json(greeting)
            # Persist so next reconnect with same session_id sees history and skips greeting
            async with AsyncSessionLocal() as db:
                await _save(db, session_id, "agent", greeting_text)

        while True:
            data = await websocket.receive_json()
            if data.get("type") != "msg":
                continue
            user_content: str = str(data.get("content", "")).strip()
            if not user_content:
                continue

            # Persist user message
            async with AsyncSessionLocal() as db:
                await _save(db, session_id, "user", user_content)
                history = await _history(db, session_id)
                sess = await db.scalar(
                    select(ChatSession).where(ChatSession.session_id == session_id)
                )
                human_active = sess.human_active if sess else False

            # Publish to admin channel
            await pubsub.publish(
                pubsub.ADMIN_CHANNEL,
                _envelope("user", user_content, session_id),
            )

            if human_active:
                # Human has taken over — don't run LLM, just let admin reply via Redis
                await websocket.send_json(
                    _envelope("system", "Ray is reviewing your message…", session_id)
                )
                continue

            # Run LLM
            try:
                reply, tool_name = await run_agent(history, session_id)
            except Exception as exc:
                reply = "Sorry, I hit a snag. Try again in a moment."
                tool_name = None

            async with AsyncSessionLocal() as db:
                await _save(db, session_id, "agent", reply, tool_call=tool_name)

            # If LLM escalated, flip human_active flag
            if tool_name == "escalate_to_human":
                async with AsyncSessionLocal() as db:
                    sess = await db.scalar(
                        select(ChatSession).where(ChatSession.session_id == session_id)
                    )
                    if sess:
                        sess.human_active = True
                        sess.status = "escalated"
                        await db.commit()

            agent_msg = _envelope("agent", reply, session_id,
                                  **({"tool": tool_name} if tool_name else {}))
            await websocket.send_json(agent_msg)
            await pubsub.publish(pubsub.ADMIN_CHANNEL, agent_msg)

    except WebSocketDisconnect:
        pass
    finally:
        redis_task.cancel()
        await ps.unsubscribe(pubsub.session_channel(session_id))


async def _is_admin_token(token: str) -> bool:
    """Accept the static PORTFOLIO_ADMIN_TOKEN OR a valid portfolio admin JWT."""
    from app.core.config import settings
    if not token:
        return False
    if token == settings.PORTFOLIO_ADMIN_TOKEN:
        return True
    # Validate as portfolio admin JWT
    if not settings.PORTFOLIO_API_URL:
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                f"{settings.PORTFOLIO_API_URL}/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code == 200:
                data = r.json()
                return bool(data.get("is_superuser") or "admin" in str(data.get("roles", [])))
    except Exception:
        pass
    return False


@router.websocket("/ws/admin")
async def admin_ws(websocket: WebSocket) -> None:
    """Admin WebSocket — subscribes to all sessions via the admin pub/sub channel.

    Query param: ?token=<PORTFOLIO_ADMIN_TOKEN or portfolio admin JWT>
    Accepts incoming JSON: {"type": "reply", "session_id": "...", "content": "..."}
    to send a human reply into a specific session.
    """
    token = websocket.query_params.get("token", "")
    if not await _is_admin_token(token):
        await websocket.close(code=4001)
        return

    await websocket.accept()
    ps = await pubsub.subscribe(pubsub.ADMIN_CHANNEL)

    async def _listener() -> None:
        async for raw in ps.listen():
            if raw["type"] != "message":
                continue
            try:
                await websocket.send_json(json.loads(raw["data"]))
            except Exception:
                break

    listen_task = asyncio.create_task(_listener())

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") != "reply":
                continue
            target = data.get("session_id", "")
            content = str(data.get("content", "")).strip()
            if not target or not content:
                continue

            async with AsyncSessionLocal() as db:
                await _save(db, target, "human", content)

            msg = _envelope("human", content, target)
            await pubsub.publish(pubsub.session_channel(target), msg)

    except WebSocketDisconnect:
        pass
    finally:
        listen_task.cancel()
        await ps.unsubscribe(pubsub.ADMIN_CHANNEL)
