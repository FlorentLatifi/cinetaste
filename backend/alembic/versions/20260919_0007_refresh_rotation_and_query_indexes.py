"""Refresh-token successor link + composite indexes for hot queries.

Revision ID: 20260919_0007
Revises: 20260717_0006
Create Date: 2026-09-19

* ``refresh_tokens.replaced_by_id`` distinguishes a rotated token from one
  revoked by logout or reuse detection, which enables a short grace window for
  concurrent refreshes (two tabs) instead of logging the user out.
* Composite indexes match the queries that run on every request:
  profile recompute (events by user), For You exclusions (states by user +
  state) and the history keyset page (user, updated_at, title_id).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260919_0007"
down_revision: str | None = "20260717_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "refresh_tokens",
        sa.Column("replaced_by_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_refresh_tokens_replaced_by_id",
        "refresh_tokens",
        "refresh_tokens",
        ["replaced_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_interaction_events_user_created",
        "interaction_events",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_user_title_state_user_state",
        "user_title_state",
        ["user_id", "state"],
    )
    op.create_index(
        "ix_user_title_state_user_updated_title",
        "user_title_state",
        ["user_id", "updated_at", "title_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_title_state_user_updated_title", table_name="user_title_state")
    op.drop_index("ix_user_title_state_user_state", table_name="user_title_state")
    op.drop_index("ix_interaction_events_user_created", table_name="interaction_events")
    op.drop_constraint("fk_refresh_tokens_replaced_by_id", "refresh_tokens", type_="foreignkey")
    op.drop_column("refresh_tokens", "replaced_by_id")
