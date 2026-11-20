"""Catalog ingestion against Postgres with a fake TMDb client (no network)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.catalog_discovery import DiscoveryPlan
from app.application.catalog_ingest import CatalogIngestService
from app.domain.exceptions import AppError
from app.infrastructure.db.models.catalog import Credit, Person, Title

pytestmark = pytest.mark.integration

FAILING_ID = 9_000_001
MALFORMED_ID = 9_000_002
TV_ID = 9_100_001
RATED_ID = 9_000_020
KOREAN_ID = 9_000_021
DECADE_ID = 9_000_022
TV_COLLISION_ID = 9_000_023


class FakeTmdb:
    def __init__(self) -> None:
        self.detail_calls = 0

    async def get_genres(self, media_type: str) -> list[dict[str, Any]]:
        return [{"id": 18, "name": "Drama"}, {"id": 35, "name": "Comedy"}]

    async def discover(
        self, media_type: str, *, page: int = 1, sort_by: str = "", **filters: Any
    ) -> list[dict]:
        if page > 1:
            return []
        if filters.get("original_language") == "ko":
            return [{"id": KOREAN_ID}, {"id": 9_000_010}]  # one new, one also mainstream
        if filters.get("released_from"):
            return [{"id": DECADE_ID}]
        if sort_by == "vote_average.desc":
            return [{"id": RATED_ID}, {"id": TV_COLLISION_ID}] if media_type == "movie" else []
        if media_type == "tv":
            return [{"id": TV_ID}]
        return [{"id": i} for i in (9_000_010, 9_000_011, FAILING_ID, MALFORMED_ID)]

    def _movie(self, tmdb_id: int) -> dict[str, Any]:
        return {
            "id": tmdb_id,
            "title": f"Movie {tmdb_id}",
            "overview": "A detective hunts a smuggler through the harbor.",
            "release_date": "2015-05-01",
            "runtime": 110,
            "popularity": 12.0,
            "vote_average": 7.4,
            "vote_count": 900,
            "poster_path": f"/p{tmdb_id}.jpg",
            "original_language": "en",
            "genres": [{"id": 18, "name": "Drama"}],
            "keywords": {"keywords": [{"id": 1, "name": "neo-noir"}, {"id": 2, "name": "harbor"}]},
            "credits": {
                "cast": [
                    {"id": 501, "name": "Lead Actor", "order": 0},
                    # TMDb sometimes lists one person twice (two characters).
                    {"id": 501, "name": "Lead Actor", "order": 3},
                ],
                "crew": [{"id": 601, "name": "Ava Voss", "job": "Director"}],
            },
            "production_countries": [{"iso_3166_1": "US"}],
        }

    async def get_movie(self, tmdb_id: int) -> dict[str, Any]:
        self.detail_calls += 1
        if tmdb_id == FAILING_ID:
            raise AppError("TMDB rate limit exceeded", status_code=503, code="tmdb_rate_limited")
        payload = self._movie(tmdb_id)
        if tmdb_id == MALFORMED_ID:
            payload["genres"] = [{"name": "No id"}]
        return payload

    async def get_tv(self, tmdb_id: int) -> dict[str, Any]:
        self.detail_calls += 1
        return {
            "id": tmdb_id,
            "name": "Harbor Hymn",
            "overview": "A coastal town hides a secret.",
            "first_air_date": "2019-03-01",
            "episode_run_time": [52],
            "vote_average": 8.2,
            "vote_count": 400,
            "genres": [{"id": 18, "name": "Drama"}],
            "keywords": {"results": [{"id": 3, "name": "small town"}]},
            "credits": {"cast": [{"id": 701, "name": "Coastal Star", "order": 0}], "crew": []},
            "created_by": [{"id": 801, "name": "Mara Quinn"}],
            "origin_country": ["GB"],
        }


async def test_ingest_isolates_failures_and_is_idempotent(db_session: AsyncSession) -> None:
    tmdb = FakeTmdb()
    service = CatalogIngestService(db_session, tmdb, batch_size=10)  # type: ignore[arg-type]
    stats = await service.ingest_popular(pages=3, include_tv=True)

    seed = stats["onboarding_seed"]
    assert seed["failed"] == 0 and seed["created"] == seed["seed_requested"]
    assert stats["discovered"] == 5
    assert stats["failed"] == 2  # TMDb error + malformed payload, the rest persisted
    assert stats["created"] == 3

    total = await db_session.scalar(select(func.count()).select_from(Title))
    assert total == seed["seed_requested"] + 3

    tv = await db_session.scalar(select(Title).where(Title.external_tmdb_id == TV_ID))
    assert tv is not None and tv.media_type == "tv"
    snapshot = tv.extra["feature_snapshot"]
    assert "person:writer:mara quinn" in snapshot  # TV creator feeds the writer signal
    creator = await db_session.scalar(
        select(Credit.job)
        .join(Person, Person.id == Credit.person_id)
        .where(Credit.title_id == tv.id, Person.name == "Mara Quinn")
    )
    assert creator == "Creator"
    assert tv.embedding is not None

    rerun = await CatalogIngestService(db_session, FakeTmdb(), batch_size=10).ingest_popular(  # type: ignore[arg-type]
        pages=3, include_tv=True
    )
    assert rerun["created"] == 0
    assert rerun["updated"] == 3
    assert await db_session.scalar(select(func.count()).select_from(Title)) == total


DISCOVERY = DiscoveryPlan(
    rated_pages=1,
    languages=("ko",),
    language_pages=1,
    decades=(1990,),
    decade_pages=1,
    tv_rated_pages=0,
    tv_language_pages=0,
)


async def test_discovery_passes_add_titles_and_rerun_is_idempotent_and_cheap(
    db_session: AsyncSession,
) -> None:
    first_tmdb = FakeTmdb()
    stats = await CatalogIngestService(db_session, first_tmdb, batch_size=10).ingest_popular(  # type: ignore[arg-type]
        pages=3, include_tv=True, discovery=DISCOVERY
    )
    # mainstream 5 (2 fail) + rated, korean, decade, collision-id movie
    assert stats["discovered"] == 9
    assert stats["created"] == 3 + 4 and stats["failed"] == 2
    assert stats["failed_passes"] == []
    for tmdb_id in (RATED_ID, KOREAN_ID, DECADE_ID):
        assert await db_session.scalar(select(Title.id).where(Title.external_tmdb_id == tmdb_id))
    total = await db_session.scalar(select(func.count()).select_from(Title))

    # Re-run: discovery-only titles are not fetched again; mainstream ones refresh.
    second_tmdb = FakeTmdb()
    rerun = await CatalogIngestService(db_session, second_tmdb, batch_size=10).ingest_popular(  # type: ignore[arg-type]
        pages=3, include_tv=True, discovery=DISCOVERY
    )
    assert rerun["created"] == 0
    assert rerun["skipped_existing"] == 4  # rated, korean, decade, collision-id movie
    assert rerun["updated"] == 3
    # Detail calls: seed deck + the mainstream targets only.
    seed = rerun["onboarding_seed"]["seed_requested"]
    assert second_tmdb.detail_calls == seed + 5
    assert await db_session.scalar(select(func.count()).select_from(Title)) == total

    # Resume after an interrupted run: refresh_existing re-fetches everything.
    third = FakeTmdb()
    refreshed = await CatalogIngestService(db_session, third, batch_size=10).ingest_popular(  # type: ignore[arg-type]
        pages=3, include_tv=True, discovery=DISCOVERY, refresh_existing=True
    )
    assert refreshed["skipped_existing"] == 0 and refreshed["updated"] == 7
    assert await db_session.scalar(select(func.count()).select_from(Title)) == total


async def test_tv_id_colliding_with_a_stored_movie_never_overwrites_it(
    db_session: AsyncSession,
) -> None:
    """external_tmdb_id is unique across media types; TMDb numbers them separately."""

    class CollidingTmdb(FakeTmdb):
        def __init__(self, *, mainstream: bool) -> None:
            super().__init__()
            self.mainstream = mainstream

        async def discover(self, media_type: str, *, page: int = 1, sort_by: str = "", **f: Any) -> list[dict]:
            rated_pass = sort_by == "vote_average.desc"
            if page > 1 or media_type != "tv" or rated_pass == self.mainstream:
                return []
            return [{"id": RATED_ID}]

    service = CatalogIngestService(db_session, FakeTmdb(), batch_size=10)  # type: ignore[arg-type]
    await service.upsert_movie(RATED_ID)
    await db_session.commit()

    # Discovery-only TV target: skipped as a collision before any fetch.
    stats = await CatalogIngestService(db_session, CollidingTmdb(mainstream=False), batch_size=10).ingest_popular(  # type: ignore[arg-type]
        pages=1,
        include_tv=True,
        discovery=DiscoveryPlan(
            rated_pages=0, language_pages=0, decade_pages=0, tv_rated_pages=1, tv_language_pages=0
        ),
    )
    assert stats["id_collisions"] == 1
    movie = await db_session.scalar(select(Title).where(Title.external_tmdb_id == RATED_ID))
    assert movie is not None and movie.media_type == "movie"

    # A refreshed (mainstream) TV target hits the guard in the upsert: counted failed, row intact.
    stats = await CatalogIngestService(db_session, CollidingTmdb(mainstream=True), batch_size=10).ingest_popular(  # type: ignore[arg-type]
        pages=1, include_tv=True, discovery=None
    )
    assert stats["failed"] >= 1
    await db_session.refresh(movie)
    assert movie.media_type == "movie" and movie.name == f"Movie {RATED_ID}"
