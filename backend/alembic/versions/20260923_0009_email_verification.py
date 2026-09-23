"""Email verification: a verified-at stamp on users plus one-time tokens.

Revision ID: 20260923_0009
Revises: 20260922_0008
Create Date: 2026-09-23

Registration accepted any address without proving the person owns it, so an
account could be opened under someone else's email — and that someone would
then receive the password-reset mail for an account they never made.

``users.email_verified_at`` is NULL for every existing account. They are not
backfilled as verified (that would be a lie about what was checked) and
nothing is gated on it unless ``REQUIRE_EMAIL_VERIFICATION`` is on, so this
migration changes no behaviour by itself.

The token table mirrors ``password_reset_tokens``: hash only, single use,
expiring — a stolen database row must not be usable as a link.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260923_0009"
down_revision: str | None = "20260922_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_email_verification_tokens_token_hash",
        "email_verification_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_email_verification_tokens_user_id",
        "email_verification_tokens",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_email_verification_tokens_user_id", "email_verification_tokens")
    op.drop_index("ix_email_verification_tokens_token_hash", "email_verification_tokens")
    op.drop_table("email_verification_tokens")
    op.drop_column("users", "email_verified_at")
