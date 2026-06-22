"""Create chat_sessions and chat_messages tables

Revision ID: aa0001
Revises:
Create Date: 2026-06-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "aa0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("session_id", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column("visitor_name", sa.String(128), nullable=True),
        sa.Column("visitor_email", sa.String(255), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), server_default="idle", nullable=False),
        sa.Column("human_active", sa.Boolean, server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column(
            "session_id",
            sa.String(64),
            sa.ForeignKey("chat_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("sender", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("tool_call", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
