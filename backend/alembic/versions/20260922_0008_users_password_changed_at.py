"""Track when a user's password last changed, to expire access tokens with it.

Revision ID: 20260922_0008
Revises: 20260919_0007
Create Date: 2026-09-22

Resetting a password revoked every refresh token but could not touch access
tokens, which are stateless — so a stolen token kept working for the rest of
its 15-minute life *after* the victim locked the attacker out. Recording the
change time lets ``get_current_user`` reject any token minted before it.

NULL means "never changed since registration", which is why this column is
nullable rather than backfilled with ``now()``: stamping existing rows would
log every current session out on deploy for no security gain.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260922_0008"
down_revision: str | None = "20260919_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "password_changed_at")
