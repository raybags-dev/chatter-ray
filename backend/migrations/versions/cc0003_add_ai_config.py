"""add chat_ai_config table (runtime AI provider selection)

Revision ID: cc0003
Revises: bb0002
Create Date: 2026-10-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "cc0003"
down_revision: Union[str, None] = "bb0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_ai_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False, server_default="deepseek"),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("api_key", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    # Seed the singleton: DeepSeek by default, key from env fallback.
    op.execute(
        "INSERT INTO chat_ai_config (id, provider, model, api_key, created_at, updated_at) "
        "VALUES (1, 'deepseek', NULL, NULL, now(), now()) "
        "ON CONFLICT (id) DO NOTHING"
    )


def downgrade() -> None:
    op.drop_table("chat_ai_config")
