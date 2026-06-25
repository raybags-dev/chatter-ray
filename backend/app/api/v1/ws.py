"""WebSocket endpoint — one connection per visitor session.

Flow:
  1. Client connects to /ws/{session_id}?name=<visitor_name>
  2. Server creates or resumes ChatSession in DB (stores visitor_name if provided)
  3. Server subscribes to Redis channel `chat:session:{session_id}`
  4. Client sends JSON: {"type": "msg", "content": "hello"}
                     OR {"type": "meta", "visitor_name": "Alice"}
  5. Server:
     a. Saves message to DB
     b. Publishes to Redis admin channel (admin panel sees it live)
     c. If session.human_active: acknowledges, waits for human reply via Redis
        Else: runs LLM agent, streams reply back
  6. Admin connects to /ws/admin?token=<jwt> and subscribes to all sessions

Message format sent to client:
  {"type": "msg", "sender": "user"|"agent"|"human"|"system", "content": "...", "ts": 1234}
  {"type": "typing", "sender": "agent"|"human", "session_id": "..."}
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis as pubsub
from app.core.database import AsyncSessionLocal
from app.core.llm import run_agent
from app.models import ChatMessage, ChatSession

router = APIRouter()


def _now() -> float:
    return time.time()


def _envelope(sender: str, content: str, session_id: str, **extra: Any) -> dict:
    return {"type": "msg", "sender": sender, "content": content,
            "session_id": session_id, "ts": _now(), **extra}


async def _get_or_create_session(
    db: AsyncSession,
    session_id: str,
    ip: str,
    visitor_name: str | None = None,
) -> ChatSession:
    sess = await db.scalar(
        select(ChatSession).where(ChatSession.session_id == session_id)
    )
    if not sess:
        sess = ChatSession(
            session_id=session_id,
            ip_address=ip,
            status="active",
            visitor_name=visitor_name or None,
        )
        db.add(sess)
        await db.commit()
        await db.refresh(sess)
    elif visitor_name and not sess.visitor_name:
        sess.visitor_name = visitor_name
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


async def _is_admin_token(token: str) -> bool:
    """Accept the static PORTFOLIO_ADMIN_TOKEN OR a valid portfolio admin JWT."""
    from app.core.config import settings
    if not token:
        return False
    if token == settings.PORTFOLIO_ADMIN_TOKEN:
        return True
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


# IMPORTANT: /ws/admin MUST be registered before /ws/{session_id} so FastAPI
# matches the literal path first. Parameterised routes consume everything that
# precedes them in the route list, including the literal "admin" segment.
@router.websocket("/ws/admin")
async def admin_ws(websocket: WebSocket) -> None:
    """Admin WebSocket — subscribes to all sessions via the admin pub/sub channel.

    Query param: ?token=<PORTFOLIO_ADMIN_TOKEN or portfolio admin JWT>
    Accepts incoming JSON: {"type": "reply", "session_id": "...", "content": "..."}
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
            # Deliver to visitor via session channel
            await pubsub.publish(pubsub.session_channel(target), msg)
            # Echo back to admin channel so the admin panel shows it
            await pubsub.publish(pubsub.ADMIN_CHANNEL, msg)

    except WebSocketDisconnect:
        pass
    finally:
        listen_task.cancel()
        await ps.unsubscribe(pubsub.ADMIN_CHANNEL)


@router.websocket("/ws/{session_id}")
async def visitor_ws(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    ip = websocket.client.host if websocket.client else "unknown"
    visitor_name = websocket.query_params.get("name", "").strip() or None

    try:
        async with AsyncSessionLocal() as db:
            await _get_or_create_session(db, session_id, ip, visitor_name)
        ps = await pubsub.subscribe(pubsub.session_channel(session_id))
    except Exception as exc:
        logger.error("visitor_ws setup failed for %s: %s", session_id, exc)
        await websocket.close(code=1011, reason="server_unavailable")
        return

    # Serialize all sends to prevent concurrent WebSocket frame corruption
    _send_lock = asyncio.Lock()

    async def safe_send(payload: dict) -> None:
        async with _send_lock:
            await websocket.send_json(payload)

    async def _redis_listener() -> None:
        """Forward Redis messages (human replies, typing events) to the visitor."""
        try:
            async for raw in ps.listen():
                if raw["type"] != "message":
                    continue
                try:
                    payload = json.loads(raw["data"])
                except Exception:
                    continue
                msg_type = payload.get("type")
                sender = payload.get("sender", "")
                if msg_type == "typing" or (
                    msg_type in ("msg", None)
                    and sender in ("human", "system", "agent")
                ):
                    try:
                        await safe_send(payload)
                    except Exception:
                        return
        except Exception:
            pass

    redis_task = asyncio.create_task(_redis_listener())

    try:
        async with AsyncSessionLocal() as db:
            history = await _history(db, session_id)

        if not history:
            greeting_text = (
                "Hi! I'm Raymond's AI assistant. Welcome to his portfolio — "
                "if you have any inquiries, requests, or questions, feel free to ask me."
            )
            greeting = _envelope("agent", greeting_text, session_id)
            await safe_send(greeting)
            async with AsyncSessionLocal() as db:
                await _save(db, session_id, "agent", greeting_text)

        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            # Visitor name submitted after connect (e.g. from name-prompt flow)
            if msg_type == "meta":
                vname = str(data.get("visitor_name", "")).strip()
                if vname:
                    async with AsyncSessionLocal() as db:
                        sess = await db.scalar(
                            select(ChatSession).where(ChatSession.session_id == session_id)
                        )
                        if sess and not sess.visitor_name:
                            sess.visitor_name = vname
                            await db.commit()
                    await pubsub.publish(pubsub.ADMIN_CHANNEL, {
                        "type": "session_update",
                        "session_id": session_id,
                        "visitor_name": vname,
                    })
                continue

            if msg_type != "msg":
                continue
            user_content: str = str(data.get("content", "")).strip()
            if not user_content:
                continue

            async with AsyncSessionLocal() as db:
                await _save(db, session_id, "user", user_content)
                history = await _history(db, session_id)
                sess = await db.scalar(
                    select(ChatSession).where(ChatSession.session_id == session_id)
                )
                human_active = sess.human_active if sess else False

            await pubsub.publish(
                pubsub.ADMIN_CHANNEL,
                _envelope("user", user_content, session_id),
            )

            if human_active:
                continue

            try:
                reply, tool_name = await run_agent(history, session_id)
            except Exception:
                reply = "Sorry, I hit a snag. Try again in a moment."
                tool_name = None

            async with AsyncSessionLocal() as db:
                await _save(db, session_id, "agent", reply, tool_call=tool_name)

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
            await safe_send(agent_msg)
            await pubsub.publish(pubsub.ADMIN_CHANNEL, agent_msg)

    except WebSocketDisconnect:
        pass
    finally:
        redis_task.cancel()
        await ps.unsubscribe(pubsub.session_channel(session_id))
        try:
            await pubsub.publish(pubsub.ADMIN_CHANNEL, _envelope(
                "system", "Visitor disconnected", session_id
            ))
        except Exception:
            pass
