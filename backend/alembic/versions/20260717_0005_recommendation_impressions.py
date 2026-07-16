"""recommendation impressions for offline eval

Revision ID: 20260717_0005
Revises: 20260717_0004
Create Date: 2026-07-17
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260717_0005"
down_revision: Union[str, None] = "20260717_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recommendation_impressions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reason_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["title_id"], ["titles.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_recommendation_impressions_user_id",
        "recommendation_impressions",
        ["user_id"],
    )
    op.create_index(
        "ix_recommendation_impressions_title_id",
        "recommendation_impressions",
        ["title_id"],
    )
    op.create_index(
        "ix_recommendation_impressions_slate_id",
        "recommendation_impressions",
        ["slate_id"],
    )
    op.create_index(
        "ix_recommendation_impressions_created_at",
        "recommendation_impressions",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_recommendation_impressions_created_at", table_name="recommendation_impressions")
    op.drop_index("ix_recommendation_impressions_slate_id", table_name="recommendation_impressions")
    op.drop_index("ix_recommendation_impressions_title_id", table_name="recommendation_impressions")
    op.drop_index("ix_recommendation_impressions_user_id", table_name="recommendation_impressions")
    op.drop_table("recommendation_impressions")
