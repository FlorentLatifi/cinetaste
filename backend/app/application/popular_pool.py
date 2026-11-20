"""One shared, short-lived copy of the "popular" half of the candidate pool.

Every slate is built from two queries: the user's nearest neighbours (different every
time) and the ``rec_popular_candidates`` most popular titles (the same for everyone).
The second was re-run per request — a sequential scan, then ~120 rows of 384-float
vectors that pgvector decodes from text one by one, then a second query for their
genres. Measured locally at 4,700 titles that is about a quarter of a guest slate.

Design notes:

* The rows are loaded in a session of the pool's own, never the request's. When that
  session closes they are detached with their loaded state intact. Had they been
  loaded in the request's session, a rollback there (any failed request) would expire
  them, and the next request to touch them would hit a detached, expired object.
* Read-only by contract: ranking and serialisation only read these objects.
* A title ingested within the TTL joins the popular half once it expires. Ranking
  results for a given pool are unchanged; only the freshness of this one half of the
  pool is bounded, the same trade the cached guest deck already makes.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.infrastructure.db.models.catalog import Title

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def candidate_query():  # noqa: ANN201
    """Titles that can be ranked, with the relationship ranking and the API read."""
    return select(Title).where(Title.embedding.is_not(None)).options(selectinload(Title.genres))


class PopularPool:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._ttl = ttl_seconds
        self._clock = clock
        self._limit = 0
        self._expires_at = 0.0
        self._titles: tuple[Title, ...] = ()
        self.loads = 0  # how many times the database was asked (tests, diagnostics)

    def clear(self) -> None:
        self._titles = ()
        self._expires_at = 0.0

    async def get(self, limit: int) -> tuple[Title, ...]:
        now = self._clock()
        if self._titles and self._limit == limit and now < self._expires_at:
            return self._titles
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    candidate_query().order_by(Title.popularity.desc()).limit(limit)
                )
            ).all()
        self.loads += 1
        titles = tuple(rows)
        if titles and self._ttl > 0:
            self._titles, self._limit, self._expires_at = titles, limit, now + self._ttl
        return titles


_pool: PopularPool | None = None


def get_popular_pool(ttl_seconds: float) -> PopularPool | None:
    """The process-wide pool, or None when disabled (ttl 0)."""
    global _pool
    if ttl_seconds <= 0:
        return None
    if _pool is None:
        from app.infrastructure.db.session import async_session_factory

        _pool = PopularPool(async_session_factory, ttl_seconds=ttl_seconds)
    return _pool


def reset_popular_pool() -> None:
    """Drop the shared pool (test isolation only)."""
    global _pool
    _pool = None


__all__ = ["PopularPool", "candidate_query", "get_popular_pool", "reset_popular_pool"]
