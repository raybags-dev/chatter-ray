"""SQLAlchemy models for the chat service.

Tables live in the shared portfolio Postgres DB under the `chat_` prefix.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    visitor_name: Mapped[str | None] = mapped_column(String(128))
    visitor_email: Mapped[str | None] = mapped_column(String(255))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    # idle|active|escalated|closed
    status: Mapped[str] = mapped_column(String(32), default="idle")
    # whether a human admin has taken over from the LLM
    human_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.id"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chat_sessions.session_id", ondelete="CASCADE"), index=True
    )
    # user | agent | human
    sender: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # tool call name if sender=agent and a tool was invoked
    tool_call: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class ChatGuardrail(Base):
    __tablename__ = "chat_guardrails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # hard_block | soft_redirect | topic_scope | injection_defense
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    rule: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ChatAIConfig(Base):
    """Singleton row (id=1): which AI provider the chat agent uses.

    Editable at runtime from the portfolio admin panel; the LLM reads it with
    a short TTL cache. api_key is nullable - when empty the provider falls back
    to its server-side env key (DEEPSEEK_API_KEY, GROQ_API_KEY, ...).
    """

    __tablename__ = "chat_ai_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # deepseek | groq | claude | gemini
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="deepseek")
    # optional model override; empty = provider default
    model: Mapped[str | None] = mapped_column(String(128))
    # optional per-provider API key; empty = env fallback
    api_key: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
