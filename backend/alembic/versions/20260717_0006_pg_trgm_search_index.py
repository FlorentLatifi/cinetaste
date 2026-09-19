"""pg_trgm GIN index for fuzzy title search

Revision ID: 20260717_0006
Revises: 20260717_0005
Create Date: 2026-07-17
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_0006"
down_revision: Union[str, None] = "20260717_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_titles_name_trgm",
        "titles",
        [sa.text("name gin_trgm_ops")],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_titles_original_name_trgm",
        "titles",
        [sa.text("original_name gin_trgm_ops")],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_titles_original_name_trgm", table_name="titles")
    op.drop_index("ix_titles_name_trgm", table_name="titles")
