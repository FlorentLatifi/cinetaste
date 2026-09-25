from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any
from uuid import UUID, uuid4

from anyio import to_thread
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.application.history_cursor import (
    CursorError,
    decode_history_cursor,
    encode_history_cursor,
)
from app.application.taste_service import load_profile_titles
from app.core.config import Settings
from app.data.onboarding_seed import (
    load_onboarding_seed_deck,
    order_titles_by_seed,
    pick_diverse_fallback,
)
from app.domain.exceptions import AppError
from app.domain.taste_signals import (
    FEED_EXCLUDE_STATES,
    HISTORY_VISIBLE_STATES,
    effective_title_signals,
    weight_for,
)
from app.infrastructure.cache import get_store
from app.infrastructure.db.models.catalog import Credit, Title
from app.infrastructure.db.models.interaction import RecommendationImpression, UserTitleState
from app.infrastructure.db.models.taste import TasteProfile
from app.recommendation.explanations import Reason, strip_explain_memory
from app.recommendation.pipeline import RankedItem, rank_titles
from app.recommendation.profile import build_profile

logger = logging.getLogger(__name__)

# pgvector's HNSW search only explores ``hnsw.ef_search`` candidates (default 40)
# and filters afterwards, so LIMIT 250 would silently return ≤ 40 rows.
_HNSW_EF_SEARCH_MAX = 1000
# Extra candidates fetched to replace the ones ranking will exclude. Bounded:
# ef_search tracks the fetch size, so this trades graph-search cost for pool
# size and the return diminishes quickly.
_ANN_OVERFETCH_MAX = 150
_WATCHLIST_LIMIT = 200


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(slots=True)
class Slate:
    items: list[tuple[Title, RankedItem]]
    slate_id: UUID
    # True when ranked for this request, False when served from cache.
    fresh: bool


class RecommendationService:
    """Candidate generation + ranking. Taste *weights* live in domain.taste_signals."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    # ------------------------------------------------------------------ For You

    async def _load_candidates(
        self,
        *,
        user_vector: list[float] | None,
        exclude_ids: set[UUID],
    ) -> list[Title]:
        """Build a candidate pool for ranking.

        Warm profiles: pgvector nearest neighbours (cosine, HNSW index) plus a
        popular slice so we don't overfit to nearest neighbours only.
        Cold start (no vector): popularity-ordered pool.
        """
        base = select(Title).where(Title.embedding.is_not(None)).options(selectinload(Title.genres))
        # Deliberately no `NOT IN (exclude_ids)`: rank_titles filters the same
        # set again, so this only ever kept the candidate pool full — and the
        # list is planned and transmitted on every request. Measured at 10k
        # titles with 2,000 exclusions: 168 ms for a full pool of 250 with the
        # SQL filter, against 127 ms for 325 usable candidates by over-fetching
        # instead. Faster *and* more to rank.

        if self._settings.rec_use_ann and user_vector:
            ann_limit = max(self._settings.rec_ann_candidates, self._settings.rec_slate_size * 4)
            # Ask for extra rows to cover the ones ranking will drop. Capped
            # because ef_search follows this number, and a larger graph search
            # costs more than the candidates are worth.
            ann_limit += min(len(exclude_ids), _ANN_OVERFETCH_MAX)
            # No over-fetch here: popular candidates are the cheap half of the
            # pool and the least valuable to replace. Measured — over-fetching
            # both queries cost 188 ms at 500 exclusions against 107 ms for the
            # SQL filter it replaced, because the extra rows have to be
            # hydrated whether ranking uses them or not.
            pop_limit = max(self._settings.rec_popular_candidates, 20)
            ann_rows: list[Title] = []
            try:
                # Savepoint: a failed query aborts the whole Postgres transaction,
                # which would also break the popularity fallback below.
                async with self._session.begin_nested():
                    ef_search = min(max(ann_limit, 40), _HNSW_EF_SEARCH_MAX)
                    await self._session.execute(
                        select(func.set_config("hnsw.ef_search", str(ef_search), True))
                    )
                    ann_rows = list(
                        (
                            await self._session.scalars(
                                base.order_by(Title.embedding.cosine_distance(user_vector)).limit(
                                    ann_limit
                                )
                            )
                        ).all()
                    )
            except Exception:
                logger.warning("ann_candidate_query_failed; falling back to popularity", exc_info=True)

            pop_rows = (
                await self._session.scalars(base.order_by(Title.popularity.desc()).limit(pop_limit))
            ).all()
            merged: dict[UUID, Title] = {}
            for title in [*ann_rows, *pop_rows]:
                merged[title.id] = title
            if merged:
                return list(merged.values())

        pool = max(
            self._settings.rec_ann_candidates + self._settings.rec_popular_candidates, 400
        ) + min(len(exclude_ids), _ANN_OVERFETCH_MAX)
        return list(
            (await self._session.scalars(base.order_by(Title.popularity.desc()).limit(pool))).all()
        )

    async def for_you(self, user_id: UUID, *, limit: int | None = None) -> Slate:
        slate_size = limit or self._settings.rec_slate_size
        profile = await self._session.get(TasteProfile, user_id)
        profile_version = profile.version if profile else 0
        # The profile version changes on every interaction, so a new action never
        # serves a stale slate; the TTL only bounds memory.
        cache_key = f"slate:{user_id}:{profile_version}:{slate_size}"
        store = get_store()

        cached = await store.get(cache_key)
        if cached:
            try:
                payload = json.loads(cached)
                return Slate(
                    items=await self._hydrate(payload["items"]),
                    slate_id=UUID(payload["slate_id"]),
                    fresh=False,
                )
            except Exception:
                logger.warning("slate_cache_payload_invalid key=%s", cache_key, exc_info=True)

        exclude_ids = set(
            (
                await self._session.scalars(
                    select(UserTitleState.title_id).where(
                        UserTitleState.user_id == user_id,
                        UserTitleState.state.in_(list(FEED_EXCLUDE_STATES)),
                    )
                )
            ).all()
        )

        user_vector = list(profile.vector) if profile is not None and profile.vector is not None else None
        raw_features = dict(profile.features) if profile and profile.features else {}
        titles, ranked = await self._rank_for_profile(
            user_vector=user_vector,
            raw_features=raw_features,
            exclude_ids=exclude_ids,
            slate_size=slate_size,
            who=str(user_id),
        )

        slate_id = uuid4()
        items_payload = [
            {
                "title_id": str(item.title_id),
                "score": item.score,
                # `evidence` is deliberately absent. It was 95% of a 16.5 KiB
                # payload (781 of ~824 bytes per item) and nothing renders it —
                # the user-facing value is entirely in `message`.
                "reasons": [{"code": r.code, "message": r.message} for r in item.reasons],
            }
            for item in ranked
        ]
        await store.set(
            cache_key,
            json.dumps({"slate_id": str(slate_id), "items": items_payload}),
            ttl_seconds=self._settings.rec_cache_ttl_seconds,
        )
        # Record the key so invalidate_user can delete it without a keyspace scan.
        await store.track(
            self._slate_index_key(user_id),
            cache_key,
            ttl_seconds=self._settings.rec_cache_ttl_seconds,
        )
        by_id = {t.id: t for t in titles}
        return Slate(
            items=[(by_id[item.title_id], item) for item in ranked],
            slate_id=slate_id,
            fresh=True,
        )

    async def _rank_for_profile(
        self,
        *,
        user_vector: list[float] | None,
        raw_features: dict[str, Any],
        exclude_ids: set[UUID],
        slate_size: int,
        who: str,
    ) -> tuple[list[Title], list[RankedItem]]:
        """Candidates + ranking for one taste profile, stored or not."""
        user_features, explain_memory = strip_explain_memory(raw_features)

        titles = await self._load_candidates(user_vector=user_vector, exclude_ids=exclude_ids)
        if titles and not any(t.embedding is not None for t in titles):
            # Ranking drops every title whose embedding is NULL, so this returns
            # an empty slate with a 200 and no other sign that anything is
            # wrong. It means ingest ran and re-embed did not.
            logger.warning(
                "slate_candidates_unembedded user_id=%s candidates=%d — "
                "For You will be empty; run: python -m app.scripts.reembed_catalog",
                who,
                len(titles),
            )

        # Ranking is CPU work (numpy + Python); keep it off the event loop.
        ranked = await to_thread.run_sync(
            partial(
                rank_titles,
                user_vector=user_vector,
                user_features=user_features,
                titles=titles,
                exclude_ids=exclude_ids,
                slate_size=slate_size,
                mmr_lambda=self._settings.rec_mmr_lambda,
                exploration_slots=self._settings.rec_exploration_slots,
                explain_memory=explain_memory,
            )
        )
        return titles, ranked

    async def guest_slate(
        self, reactions: list[tuple[UUID, str]], *, limit: int
    ) -> list[tuple[Title, RankedItem]]:
        """Rank a slate from card answers that are never stored.

        Same profile builder and ranker as For You, so a guest sees what an
        account with the same answers would see — including "because you liked
        X" reasons. Nothing is written: no events, no profile, no impressions,
        no cache entry keyed to a person.
        """
        start = datetime.now(UTC)
        # Answer order is the event order, so a changed answer supersedes the
        # first one exactly as it would in a stored log. No time decay: every
        # answer is seconds old.
        signals = effective_title_signals(
            (
                (title_id, event_type, weight_for(event_type), start + timedelta(microseconds=i))
                for i, (title_id, event_type) in enumerate(reactions)
            ),
            now=start,
        )
        built = build_profile(
            signals.values(), await load_profile_titles(self._session, list(signals))
        )
        titles, ranked = await self._rank_for_profile(
            user_vector=built.vector,
            raw_features=built.features_with_memory(),
            # Every card the guest answered, "haven't seen" included: they have
            # just been shown it and passed.
            exclude_ids={title_id for title_id, _event in reactions},
            slate_size=limit,
            who="guest",
        )
        by_id = {t.id: t for t in titles}
        return [(by_id[item.title_id], item) for item in ranked]

    @staticmethod
    def _slate_index_key(user_id: UUID) -> str:
        return f"slateidx:{user_id}"

    async def invalidate_user(self, user_id: UUID) -> None:
        """Drop this user's cached slates.

        Deletes the keys we recorded for them rather than scanning for a
        prefix. The old implementation used Redis SCAN, which walks the entire
        keyspace: measured at 1.1 ms with 100 keys in the store and 449 ms with
        100,000 — and this runs on every rating, watchlist add and undo, so the
        cost grew with how many *other* people were using the product.

        Correctness never depended on it: the cache key contains the profile
        version, which every one of those actions bumps, so a stale slate is
        already unreachable. This is about not leaving it in memory.
        """
        await get_store().drop_tracked(self._slate_index_key(user_id))

    async def log_impressions(
        self,
        user_id: UUID,
        ranked: list[tuple[Title, RankedItem]],
        *,
        slate_id: UUID | None = None,
    ) -> UUID | None:
        """Append For You impressions. Fail-open: never raise to callers.

        Uses a savepoint so a write failure does not abort the parent request txn.
        """
        if not self._settings.rec_log_impressions or not ranked:
            return None
        sid = slate_id or uuid4()
        try:
            async with self._session.begin_nested():
                for pos, (title, item) in enumerate(ranked):
                    codes = [r.code for r in (item.reasons or [])][:8]
                    self._session.add(
                        RecommendationImpression(
                            user_id=user_id,
                            title_id=title.id,
                            slate_id=sid,
                            position=pos,
                            score=float(item.score),
                            reason_codes=codes or None,
                        )
                    )
            return sid
        except Exception:
            logger.warning(
                "impression_log_failed user_id=%s slate_id=%s", user_id, sid, exc_info=True
            )
            return None

    async def _hydrate(self, payload: list[dict[str, Any]]) -> list[tuple[Title, RankedItem]]:
        ids = [UUID(item["title_id"]) for item in payload]
        if not ids:
            return []
        titles = (
            await self._session.scalars(
                select(Title).where(Title.id.in_(ids)).options(selectinload(Title.genres))
            )
        ).all()
        by_id = {t.id: t for t in titles}
        result: list[tuple[Title, RankedItem]] = []
        for item in payload:
            tid = UUID(item["title_id"])
            title = by_id.get(tid)
            if title is None:
                continue
            reasons = [
                Reason(code=r["code"], message=r["message"], evidence=r.get("evidence") or {})
                for r in item.get("reasons", [])
            ]
            result.append((title, RankedItem(title_id=tid, score=float(item["score"]), reasons=reasons)))
        return result

    # --------------------------------------------------------------- Onboarding

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
        skip = exclude_ids or set()
        seed_ids = load_onboarding_seed_deck().tmdb_ids()

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
        picked = [
            t for t in order_titles_by_seed(list(seeded_rows), seed_ids) if t.id not in skip
        ][:limit]
        if len(picked) >= limit:
            return picked

        need = limit - len(picked)
        picked_ids = {t.id for t in picked} | skip
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
                .limit(max(200, limit * 10 + len(skip)))
            )
        ).all()
        picked.extend(
            pick_diverse_fallback(
                list(pool),
                limit=need,
                exclude_ids=picked_ids,
                max_per_genre=2,
                max_per_decade=3,
                max_per_language=4,
            )
        )
        return picked

    # ------------------------------------------------------------------ Catalog

    async def search(self, query: str, *, limit: int = 20) -> list[Title]:
        """Title search: substring match plus pg_trgm fuzzy match (typos),
        ordered by trigram similarity, then popularity."""
        raw = query.strip()
        if len(raw) < 2:
            return []
        pattern = f"%{_escape_like(raw)}%"
        similarity = func.greatest(
            func.similarity(Title.name, raw),
            func.similarity(func.coalesce(Title.original_name, ""), raw),
        )
        return list(
            (
                await self._session.scalars(
                    select(Title)
                    .where(
                        or_(
                            Title.name.ilike(pattern, escape="\\"),
                            Title.original_name.ilike(pattern, escape="\\"),
                            Title.name.op("%")(raw),
                        )
                    )
                    .options(selectinload(Title.genres))
                    .order_by(similarity.desc(), Title.popularity.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def existing_title_ids(self, title_ids: list[UUID]) -> set[UUID]:
        if not title_ids:
            return set()
        rows = await self._session.scalars(select(Title.id).where(Title.id.in_(title_ids)))
        return set(rows.all())

    async def title_exists(self, title_id: UUID) -> bool:
        return (
            await self._session.scalar(select(func.count()).select_from(Title).where(Title.id == title_id))
        ) == 1

    async def get_title(self, title_id: UUID) -> Title | None:
        return await self._session.scalar(
            select(Title)
            .where(Title.id == title_id)
            .options(
                selectinload(Title.genres),
                selectinload(Title.keywords),
                selectinload(Title.credits).selectinload(Credit.person),
            )
        )

    async def similar_titles(self, title_id: UUID, *, limit: int = 12) -> list[Title]:
        """Nearest neighbours in embedding space (uses HNSW when available)."""
        source = await self._session.get(Title, title_id)
        if source is None:
            return []
        neighbours = (
            select(Title)
            .where(Title.id != title_id, Title.embedding.is_not(None))
            .options(selectinload(Title.genres))
            .limit(limit)
        )
        if source.embedding is None:
            return list(
                (await self._session.scalars(neighbours.order_by(Title.popularity.desc()))).all()
            )
        try:
            async with self._session.begin_nested():
                return list(
                    (
                        await self._session.scalars(
                            neighbours.order_by(Title.embedding.cosine_distance(list(source.embedding)))
                        )
                    ).all()
                )
        except Exception:
            logger.warning("similar_titles_ann_failed title_id=%s", title_id, exc_info=True)
            return list(
                (await self._session.scalars(neighbours.order_by(Title.popularity.desc()))).all()
            )

    # ------------------------------------------------------------------ Library

    async def watchlist(self, user_id: UUID) -> list[Title]:
        """Saved titles, most recently saved first."""
        rows = (
            await self._session.execute(
                select(Title, UserTitleState.updated_at)
                .join(UserTitleState, UserTitleState.title_id == Title.id)
                .where(UserTitleState.user_id == user_id, UserTitleState.state == "watchlist")
                .options(selectinload(Title.genres))
                .order_by(UserTitleState.updated_at.desc(), Title.id)
                .limit(_WATCHLIST_LIMIT)
            )
        ).all()
        return [row[0] for row in rows]

    async def history(
        self,
        user_id: UUID,
        *,
        limit: int = 50,
        state: str | None = None,
        cursor: str | None = None,
    ) -> tuple[list[tuple[Title, UserTitleState]], str | None]:
        """Current user–title relationships, newest first, keyset-paginated.

        Returns ``(rows, next_cursor)``. ``next_cursor`` is None when no more pages.
        """
        allowed = list(HISTORY_VISIBLE_STATES)
        if state is not None:
            if state not in HISTORY_VISIBLE_STATES:
                return [], None
            allowed = [state]

        page_size = max(1, min(int(limit), 100))
        filters = [
            UserTitleState.user_id == user_id,
            UserTitleState.state.in_(allowed),
        ]
        if cursor:
            try:
                cursor_ts, cursor_title_id = decode_history_cursor(
                    cursor, secret=self._settings.jwt_secret
                )
            except CursorError as exc:
                raise AppError(str(exc), status_code=400, code="invalid_cursor") from exc
            # updated_at DESC, title_id DESC keyset
            filters.append(
                or_(
                    UserTitleState.updated_at < cursor_ts,
                    and_(
                        UserTitleState.updated_at == cursor_ts,
                        UserTitleState.title_id < cursor_title_id,
                    ),
                )
            )

        states = (
            await self._session.scalars(
                select(UserTitleState)
                .where(*filters)
                .order_by(UserTitleState.updated_at.desc(), UserTitleState.title_id.desc())
                .limit(page_size + 1)
            )
        ).all()
        has_more = len(states) > page_size
        page = list(states[:page_size])
        if not page:
            return [], None

        titles = (
            await self._session.scalars(
                select(Title)
                .where(Title.id.in_([s.title_id for s in page]))
                .options(selectinload(Title.genres))
            )
        ).all()
        by_id = {t.id: t for t in titles}
        rows = [(by_id[s.title_id], s) for s in page if s.title_id in by_id]

        next_cursor = None
        if has_more:
            last = page[-1]
            next_cursor = encode_history_cursor(
                last.updated_at, last.title_id, secret=self._settings.jwt_secret
            )
        return rows, next_cursor
