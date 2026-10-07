"""Pending registrations: no account until the email is proven.

Revision ID: 20261007_0010
Revises: 20260923_0009
Create Date: 2026-10-07

With verification required, sign-up used to create the user straight away and
gate it afterwards. The address was then taken by whoever typed it first, even
if they did not own it. Sign-up now writes a row here; the user is created only
when the mailed link is opened with the password chosen at sign-up.

Nothing reads this table unless REQUIRE_EMAIL_VERIFICATION is on, so the
migration changes no behaviour by itself.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261007_0010"
down_revision: str | None = "20260923_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_registrations",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_pending_registrations_email", "pending_registrations", ["email"], unique=True
    )
    op.create_index(
        "ix_pending_registrations_token_hash",
        "pending_registrations",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_pending_registrations_expires_at", "pending_registrations", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_pending_registrations_expires_at", "pending_registrations")
    op.drop_index("ix_pending_registrations_token_hash", "pending_registrations")
    op.drop_index("ix_pending_registrations_email", "pending_registrations")
    op.drop_table("pending_registrations")
