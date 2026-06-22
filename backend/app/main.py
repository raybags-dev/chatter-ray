from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.v1 import ws, sessions

app = FastAPI(
    title="Raybags Chat",
    description="Real-time chat service with LLM agent + human takeover.",
    version="0.1.0",
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
