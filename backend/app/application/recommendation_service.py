from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.application.taste_service import FEED_EXCLUDE_STATES
from app.core.config import Settings
from app.infrastructure.db.models.catalog import Title
from app.infrastructure.db.models.interaction import UserTitleState
from app.infrastructure.db.models.taste import TasteProfile
from app.infrastructure.db.redis import get_redis
from app.recommendation.pipeline import RankedItem, rank_titles


class RecommendationService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def for_you(self, user_id: UUID, *, limit: int | None = None) -> list[tuple[Title, RankedItem]]:
        slate_size = limit or self._settings.rec_slate_size
        profile = await self._session.get(TasteProfile, user_id)
        profile_version = profile.version if profile else 0
        cache_key = f"slate:{user_id}:{profile_version}:{slate_size}"

        redis = await get_redis()
        cached = await redis.get(cache_key)
        if cached:
            payload = json.loads(cached)
            return await self._hydrate(payload)

        exclude_states = (
            await self._session.scalars(
                select(UserTitleState).where(
                    UserTitleState.user_id == user_id,
                    UserTitleState.state.in_(list(FEED_EXCLUDE_STATES)),
                )
            )
        ).all()
        # Haven't-seen is NOT excluded — user may still want those recommended.
        exclude_ids = {row.title_id for row in exclude_states}

        titles = (
            await self._session.scalars(
                select(Title)
                .where(Title.embedding.is_not(None))
                .options(
                    selectinload(Title.genres),
                    selectinload(Title.keywords),
                    selectinload(Title.credits),
                )
                .order_by(Title.popularity.desc())
                .limit(800)
            )
        ).all()

        user_vector = None
        if profile is not None and profile.vector is not None:
            user_vector = list(profile.vector)
        user_features = dict(profile.features) if profile and profile.features else {}

        ranked = rank_titles(
            user_vector=user_vector,
            user_features=user_features,
            titles=list(titles),
            exclude_ids=exclude_ids,
            slate_size=slate_size,
            mmr_lambda=self._settings.rec_mmr_lambda,
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
        await redis.set(
            cache_key,
            json.dumps(cache_payload),
            ex=self._settings.rec_cache_ttl_seconds,
        )
        return await self._hydrate(cache_payload)

    async def invalidate_user(self, user_id: UUID) -> None:
        redis = await get_redis()
        # Simple pattern delete for local Redis
        async for key in redis.scan_iter(match=f"slate:{user_id}:*"):
            await redis.delete(key)

    async def onboarding_cards(
        self,
        *,
        limit: int = 24,
        exclude_ids: set[UUID] | None = None,
    ) -> list[Title]:
        """Diverse popular titles for rating-based onboarding.

        ``exclude_ids`` lets the client fetch another batch after many
        “Haven't seen it” answers without repeating titles.
        """
        skip = exclude_ids or set()
        # Pull a wide pool so diversity + excludes still leave enough cards.
        pool_size = max(120, limit * 6 + len(skip))
        titles = (
            await self._session.scalars(
                select(Title)
                .where(Title.poster_path.is_not(None), Title.embedding.is_not(None))
                .options(selectinload(Title.genres))
                .order_by(Title.popularity.desc())
                .limit(pool_size)
            )
        ).all()

        candidates = [t for t in titles if t.id not in skip]

        # Greedy diversity by primary genre
        picked: list[Title] = []
        genre_counts: dict[str, int] = {}
        per_genre_cap = max(3, (limit // 6) + 1)
        for title in candidates:
            primary = title.genres[0].name if title.genres else "unknown"
            if genre_counts.get(primary, 0) >= per_genre_cap:
                continue
            genre_counts[primary] = genre_counts.get(primary, 0) + 1
            picked.append(title)
            if len(picked) >= limit:
                break

        if len(picked) < limit:
            for title in candidates:
                if title not in picked:
                    picked.append(title)
                if len(picked) >= limit:
                    break
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
