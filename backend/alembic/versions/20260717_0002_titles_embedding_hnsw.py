"""Add HNSW ANN index on titles.embedding for cosine search.

Revision ID: 20260717_0002
Revises: 20260710_0001
Create Date: 2026-07-17

Enables efficient nearest-neighbor candidate generation for For You
instead of scanning a popularity-ordered heap only.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "20260717_0002"
down_revision: Union[str, None] = "20260710_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ensure extension exists (idempotent on managed hosts).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # HNSW is preferred over IVFFlat for mixed read/write and no train step.
    # vector_cosine_ops matches cosine_distance() used in candidate queries.
    # Concurrent builds are not available inside a transaction in all hosts —
    # standard create is fine for MVP catalog sizes.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_titles_embedding_hnsw
        ON titles
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_titles_embedding_hnsw")
