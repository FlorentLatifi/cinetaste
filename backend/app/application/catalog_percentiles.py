"""Catalog-wide popularity and vote-count percentiles, stored in ``Title.extra``.

Ranking decides "hidden gem" and the cold-start prior from where a title sits
in the *whole catalog*, not from raw TMDb numbers (TMDb popularity is a
trending score with no stable scale) and not from the candidate pool (which is
nearest neighbours plus the popular head, so its percentiles are biased). See
``CATALOG_PERCENTILES_KEY`` in ``app.recommendation.pipeline``.

One SQL statement computes ``percent_rank()`` over every title and merges
``{"popularity": p, "vote_count": v}`` into ``extra``; rows whose stored value
is already current are not rewritten (an UPDATE copies the whole row,
embedding included). No schema change: ``extra`` is the existing JSONB column.

Runs at the end of every catalog ingest and re-embed. To backfill or refresh
on its own::

    python -m app.scripts.reembed_catalog --percentiles-only
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.recommendation.pipeline import CATALOG_PERCENTILES_KEY

logger = logging.getLogger(__name__)

# The key is a code constant, not input, so it is inlined rather than bound:
# a bound parameter inside jsonb_build_object has no type Postgres can infer.
_REFRESH_SQL = text(
    f"""
    WITH ranked AS (
        SELECT
            id,
            jsonb_build_object(
                'popularity', round((percent_rank() OVER (ORDER BY popularity))::numeric, 4),
                'vote_count', round((percent_rank() OVER (ORDER BY vote_count))::numeric, 4)
            ) AS pct
        FROM titles
    )
    UPDATE titles AS t
    -- Not COALESCE: a Python None in a JSONB column is stored as JSON null, not
    -- SQL NULL, and `'null'::jsonb || object` builds an array.
    SET extra = (CASE WHEN jsonb_typeof(t.extra) = 'object' THEN t.extra ELSE '{{}}'::jsonb END)
        || jsonb_build_object('{CATALOG_PERCENTILES_KEY}', ranked.pct)
    FROM ranked
    WHERE t.id = ranked.id
      AND (t.extra -> '{CATALOG_PERCENTILES_KEY}') IS DISTINCT FROM ranked.pct
    """
)


async def refresh_catalog_percentiles(session: AsyncSession) -> int:
    """Recompute every title's catalog percentiles; returns rows changed.

    Does not commit: the caller owns the transaction.
    """
    result = await session.execute(_REFRESH_SQL)
    changed = int(getattr(result, "rowcount", 0) or 0)
    logger.info("catalog_percentiles_refreshed changed=%s", changed)
    return changed
