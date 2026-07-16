from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.domain.taste_signals import FEED_EXCLUDE_STATES
from app.infrastructure.db.models.catalog import Title
from app.infrastructure.db.models.interaction import UserTitleState
from app.infrastructure.db.models.taste import TasteProfile
from app.infrastructure.db.redis import get_redis
from app.recommendation.pipeline import RankedItem, rank_titles

logger = logging.getLogger(__name__)


class RecommendationService:
    """Candidate generation + ranking. Taste *weights* live in domain.taste_signals."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def _cache_get(self, key: str) -> str | None:
        try:
            redis = await get_redis()
            return await redis.get(key)
        except Exception:
            logger.warning("redis_cache_get_failed key=%s", key, exc_info=True)
            return None

    async def _cache_set(self, key: str, value: str, ex: int) -> None:
        try:
            redis = await get_redis()
            await redis.set(key, value, ex=ex)
        except Exception:
            logger.warning("redis_cache_set_failed key=%s", key, exc_info=True)

    async def _load_candidates(
        self,
        *,
        user_vector: list[float] | None,
        exclude_ids: set[UUID],
    ) -> list[Title]:
        """Build a candidate pool for ranking.

        Warm profiles: pgvector ANN (cosine) via HNSW index + a small popular
        exploration slice so we do not overfit nearest-neighbor only.

        Cold start (no vector): popularity-ordered pool.
        """
        options = (
            selectinload(Title.genres),
            selectinload(Title.keywords),
        )
        base = select(Title).where(Title.embedding.is_not(None)).options(*options)
        if exclude_ids:
            # Keep SQL filter small; ranking also filters for safety.
            base = base.where(Title.id.notin_(list(exclude_ids)))

        use_ann = (
            self._settings.rec_use_ann
            and user_vector is not None
            and len(user_vector) > 0
        )

        if use_ann:
            ann_limit = max(self._settings.rec_ann_candidates, self._settings.rec_slate_size * 4)
            pop_limit = max(self._settings.rec_popular_candidates, 20)
            try:
                ann_rows = (
                    await self._session.scalars(
                        base.order_by(Title.embedding.cosine_distance(user_vector)).limit(ann_limit)
                    )
                ).all()
            except Exception:
                # Index missing / empty table / driver issue → popularity fallback
                logger.warning("ann_candidate_query_failed; falling back to popularity", exc_info=True)
                ann_rows = []

            pop_rows = (
                await self._session.scalars(
                    base.order_by(Title.popularity.desc()).limit(pop_limit)
                )
            ).all()

            merged: dict[UUID, Title] = {}
            for t in list(ann_rows) + list(pop_rows):
                merged[t.id] = t
            if merged:
                return list(merged.values())

        # Cold start or ANN fallback
        pool = max(
            self._settings.rec_ann_candidates + self._settings.rec_popular_candidates,
            400,
        )
        return list(
            (
                await self._session.scalars(
                    base.order_by(Title.popularity.desc()).limit(pool)
                )
            ).all()
        )

    async def for_you(self, user_id: UUID, *, limit: int | None = None) -> list[tuple[Title, RankedItem]]:
        slate_size = limit or self._settings.rec_slate_size
        profile = await self._session.get(TasteProfile, user_id)
        profile_version = profile.version if profile else 0
        cache_key = f"slate:{user_id}:{profile_version}:{slate_size}"

        # Redis is optional: cache miss or Redis down both fall through to compute.
        cached = await self._cache_get(cache_key)
        if cached:
            try:
                payload = json.loads(cached)
                return await self._hydrate(payload)
            except Exception:
                logger.warning("redis_cache_payload_invalid key=%s", cache_key, exc_info=True)

        # States derived from taste signal policy (haven't_seen is never excluded).
        exclude_states = (
            await self._session.scalars(
                select(UserTitleState).where(
                    UserTitleState.user_id == user_id,
                    UserTitleState.state.in_(list(FEED_EXCLUDE_STATES)),
                )
            )
        ).all()
        exclude_ids = {row.title_id for row in exclude_states}

        from app.recommendation.explanations import strip_explain_memory

        user_vector = None
        if profile is not None and profile.vector is not None:
            user_vector = list(profile.vector)
        raw_features = dict(profile.features) if profile and profile.features else {}
        user_features, explain_memory = strip_explain_memory(raw_features)

        titles = await self._load_candidates(user_vector=user_vector, exclude_ids=exclude_ids)

        ranked = rank_titles(
            user_vector=user_vector,
            user_features=user_features,
            titles=list(titles),
            exclude_ids=exclude_ids,
            slate_size=slate_size,
            mmr_lambda=self._settings.rec_mmr_lambda,
            explain_memory=explain_memory,
        )

        cache_payload = [
            {
                "title_id": str(item.title_id),
                "score": item.score,
                "reasons": [
                    {"code": r.code, "message": r.message, "evidence": r.evidence}
                    for r in item.reasons
                ],
            }
            for item in ranked
        ]
        await self._cache_set(
            cache_key,
            json.dumps(cache_payload),
            ex=self._settings.rec_cache_ttl_seconds,
        )
        return await self._hydrate(cache_payload)

    async def invalidate_user(self, user_id: UUID) -> None:
        try:
            redis = await get_redis()
            async for key in redis.scan_iter(match=f"slate:{user_id}:*"):
                await redis.delete(key)
        except Exception:
            logger.warning("redis_invalidate_failed user_id=%s", user_id, exc_info=True)

    async def onboarding_cards(
        self,
        *,
        limit: int = 15,
        exclude_ids: set[UUID] | None = None,
    ) -> list[Title]:
        """Cold-start cards: curated seed deck first, smart diversity fallback.

        1. Prefer titles from ``app/data/onboarding_seed_deck.json`` (TMDb IDs),
           in curated order (primary batch, then reserve for “haven't seen” fill).
        2. If the seed is missing from the catalog or exhausted, fill with a
           quality + diversity scorer (not pure popularity).
        """
        from app.data.onboarding_seed import (
            load_onboarding_seed_deck,
            order_titles_by_seed,
            pick_diverse_fallback,
        )

        skip = exclude_ids or set()
        deck = load_onboarding_seed_deck()
        seed_ids = deck.tmdb_ids()

        seeded_rows = (
            await self._session.scalars(
                select(Title)
                .where(
                    Title.external_tmdb_id.in_(seed_ids),
                    Title.poster_path.is_not(None),
                    Title.embedding.is_not(None),
                )
                .options(selectinload(Title.genres))
            )
        ).all()
        seeded_ordered = [
            t for t in order_titles_by_seed(list(seeded_rows), seed_ids) if t.id not in skip
        ]
        picked: list[Title] = seeded_ordered[:limit]
        if len(picked) >= limit:
            return picked

        # Fallback pool: broader catalog, quality-aware diversity (not chart dump).
        need = limit - len(picked)
        picked_ids = {t.id for t in picked} | skip
        pool_size = max(200, limit * 10 + len(skip))
        pool = (
            await self._session.scalars(
                select(Title)
                .where(
                    Title.poster_path.is_not(None),
                    Title.embedding.is_not(None),
                    Title.media_type == "movie",
                )
                .options(selectinload(Title.genres))
                .order_by(Title.vote_count.desc())
                .limit(pool_size)
            )
        ).all()
        fill = pick_diverse_fallback(
            list(pool),
            limit=need,
            exclude_ids=picked_ids,
            max_per_genre=2,
            max_per_decade=3,
            max_per_language=4,
        )
        picked.extend(fill)
        return picked

    async def search(self, query: str, *, limit: int = 20) -> list[Title]:
        q = f"%{query.strip()}%"
        if len(query.strip()) < 2:
            return []
        return list(
            (
                await self._session.scalars(
                    select(Title)
                    .where(or_(Title.name.ilike(q), Title.original_name.ilike(q)))
                    .options(selectinload(Title.genres))
                    .order_by(Title.popularity.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def get_title(self, title_id: UUID) -> Title | None:
        return await self._session.scalar(
            select(Title)
            .where(Title.id == title_id)
            .options(
                selectinload(Title.genres),
                selectinload(Title.keywords),
                selectinload(Title.credits),
            )
        )

    async def watchlist(self, user_id: UUID) -> list[Title]:
        states = (
            await self._session.scalars(
                select(UserTitleState).where(
                    UserTitleState.user_id == user_id,
                    UserTitleState.state == "watchlist",
                )
            )
        ).all()
        if not states:
            return []
        ids = [s.title_id for s in states]
        titles = (
            await self._session.scalars(
                select(Title)
                .where(Title.id.in_(ids))
                .options(selectinload(Title.genres))
            )
        ).all()
        by_id = {t.id: t for t in titles}
        return [by_id[i] for i in ids if i in by_id]

    async def _hydrate(self, payload: list[dict[str, Any]]) -> list[tuple[Title, RankedItem]]:
        from app.recommendation.pipeline import Reason

        ids = [UUID(item["title_id"]) for item in payload]
        if not ids:
            return []
        titles = (
            await self._session.scalars(
                select(Title)
                .where(Title.id.in_(ids))
                .options(selectinload(Title.genres), selectinload(Title.keywords))
            )
        ).all()
        by_id = {t.id: t for t in titles}
        result: list[tuple[Title, RankedItem]] = []
        for item in payload:
            tid = UUID(item["title_id"])
            title = by_id.get(tid)
            if not title:
                continue
            ranked = RankedItem(
                title_id=tid,
                score=float(item["score"]),
                reasons=[
                    Reason(
                        code=r["code"],
                        message=r["message"],
                        evidence=r.get("evidence") or {},
                    )
                    for r in item.get("reasons", [])
                ],
            )
            result.append((title, ranked))
        return result
