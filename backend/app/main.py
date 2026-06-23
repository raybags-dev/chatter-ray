from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.v1 import ws, sessions

logger = logging.getLogger(__name__)


async def _periodic_cleanup() -> None:
    """Close stale (inactive >30 min) non-escalated sessions every 10 minutes."""
    from sqlalchemy import and_, func, select, update as sql_update
    from app.core.database import AsyncSessionLocal
    from app.models import ChatSession, ChatMessage

    while True:
        await asyncio.sleep(600)
        try:
            cutoff = datetime.utcnow() - timedelta(minutes=30)
            async with AsyncSessionLocal() as db:
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
                    logger.info("Auto-closed %d stale sessions", len(stale_ids))
        except Exception as exc:
            logger.error("Session cleanup error: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_periodic_cleanup())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Raybags Chat",
    description="Real-time chat service with LLM agent + human takeover.",
    version="0.1.0",
    lifespan=lifespan,
)

origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ws.router)
app.include_router(sessions.router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
