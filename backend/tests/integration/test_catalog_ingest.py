"""Catalog ingestion against Postgres with a fake TMDb client (no network)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.catalog_ingest import CatalogIngestService
from app.domain.exceptions import AppError
from app.infrastructure.db.models.catalog import Credit, Person, Title

pytestmark = pytest.mark.integration

FAILING_ID = 9_000_001
MALFORMED_ID = 9_000_002
TV_ID = 9_100_001


class FakeTmdb:
    def __init__(self) -> None:
        self.detail_calls = 0

    async def get_genres(self, media_type: str) -> list[dict[str, Any]]:
        return [{"id": 18, "name": "Drama"}, {"id": 35, "name": "Comedy"}]

    async def discover(self, media_type: str, *, page: int = 1, sort_by: str = "") -> list[dict]:
        if page > 1:
            return []
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
