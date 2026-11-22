"""Catalog ingestion against Postgres with a fake TMDb client (no network)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select, text
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


async def test_catalog_percentiles_are_written_and_refresh_is_incremental(
    db_session: AsyncSession,
) -> None:
    """Ranking reads catalog-wide percentiles from Title.extra (no schema change)."""
    from uuid import uuid4

    from app.application.catalog_percentiles import refresh_catalog_percentiles
    from app.recommendation.pipeline import CATALOG_PERCENTILES_KEY

    rows = [  # (popularity, vote_count): one huge trending outlier, as in production
        (2569.0, 30_000),
        (47.9, 20_000),
        (7.5, 16_000),  # a classic with a low trending score
        (3.0, 120),
        (1.0, 10),
    ]
    titles = [
        Title(
            id=uuid4(),
            external_tmdb_id=9_500_000 + i,
            media_type="movie",
            name=f"P{i}",
            popularity=popularity,
            vote_count=votes,
            extra={"demo": True} if i == 0 else None,
        )
        for i, (popularity, votes) in enumerate(rows)
    ]
    db_session.add_all(titles)
    await db_session.commit()

    assert await refresh_catalog_percentiles(db_session) == len(rows)
    await db_session.commit()
    for title in titles:
        await db_session.refresh(title)
    stored = [t.extra[CATALOG_PERCENTILES_KEY] for t in titles]
    assert [s["popularity"] for s in stored] == [1.0, 0.75, 0.5, 0.25, 0.0]
    assert [s["vote_count"] for s in stored] == [1.0, 0.75, 0.5, 0.25, 0.0]
    assert titles[0].extra["demo"] is True  # other keys survive the merge

    # Nothing changed, nothing rewritten.
    assert await refresh_catalog_percentiles(db_session) == 0


async def test_ingest_ends_with_catalog_percentiles_for_every_title(
    db_session: AsyncSession,
) -> None:
    from app.recommendation.pipeline import CATALOG_PERCENTILES_KEY

    service = CatalogIngestService(db_session, FakeTmdb(), batch_size=10)  # type: ignore[arg-type]
    await service.ingest_popular(pages=1, include_tv=True)
    extras = (await db_session.scalars(select(Title.extra))).all()
    assert extras and all(CATALOG_PERCENTILES_KEY in (extra or {}) for extra in extras)

    # A single re-ingested title keeps its stored value until the next refresh.
    movie = await db_session.scalar(select(Title).where(Title.external_tmdb_id == 9_000_010))
    assert movie is not None
    before = movie.extra[CATALOG_PERCENTILES_KEY]
    await service.upsert_movie(9_000_010)
    await db_session.commit()
    await db_session.refresh(movie)
    assert movie.extra[CATALOG_PERCENTILES_KEY] == before
    assert "feature_snapshot" in movie.extra


async def test_movie_collection_is_stored_and_survives_reingest_and_reembed(
    db_session: AsyncSession,
) -> None:
    """The franchise cap reads Title.extra["collection"]; ingest must keep it current."""
    from app.recommendation.pipeline import COLLECTION_KEY, collection_of
    from app.scripts import reembed_catalog

    matrix = {"id": 2344, "name": "The Matrix Collection", "poster_path": "/m.jpg"}

    class CollectionTmdb(FakeTmdb):
        def __init__(self, collection: dict[str, Any] | None) -> None:
            super().__init__()
            self.collection = collection

        async def get_movie(self, tmdb_id: int) -> dict[str, Any]:
            payload = await super().get_movie(tmdb_id)
            # TMDb always sends the field on movie detail: null for standalone films.
            payload["belongs_to_collection"] = self.collection if tmdb_id == 9_000_010 else None
            return payload

    async def stored(tmdb_id: int) -> Title:
        db_session.expire_all()
        title = await db_session.scalar(select(Title).where(Title.external_tmdb_id == tmdb_id))
        assert title is not None
        return title

    service = CatalogIngestService(db_session, CollectionTmdb(matrix), batch_size=10)  # type: ignore[arg-type]
    await service.ingest_popular(pages=1, include_tv=True)

    movie = await stored(9_000_010)
    # Only id and name are kept: poster etc. would be dead weight in every row.
    assert movie.extra[COLLECTION_KEY] == {"id": 2344, "name": "The Matrix Collection"}
    assert collection_of(movie) == 2344
    assert "feature_snapshot" in movie.extra and "catalog_percentiles" in movie.extra
    assert COLLECTION_KEY not in (await stored(9_000_011)).extra  # standalone film
    assert COLLECTION_KEY not in (await stored(TV_ID)).extra  # TV has no collections

    # Re-embed rebuilds features from the DB and must not drop the collection.
    assert await reembed_catalog.main(limit=None, force=True) == 0
    assert (await stored(9_000_010)).extra[COLLECTION_KEY]["id"] == 2344

    # Re-ingest (refresh_existing) keeps it...
    await CatalogIngestService(db_session, CollectionTmdb(matrix), batch_size=10).ingest_popular(  # type: ignore[arg-type]
        pages=1, include_tv=True, refresh_existing=True
    )
    assert collection_of(await stored(9_000_010)) == 2344

    # ...and TMDb is authoritative: a film taken out of its collection loses it.
    await CatalogIngestService(db_session, CollectionTmdb(None), batch_size=10).upsert_movie(  # type: ignore[arg-type]
        9_000_010
    )
    await db_session.commit()
    assert COLLECTION_KEY not in (await stored(9_000_010)).extra

    # A malformed value is not stored (and never breaks the ingest).
    await CatalogIngestService(  # type: ignore[arg-type]
        db_session, CollectionTmdb({"name": "no id"}), batch_size=10
    ).upsert_movie(9_000_010)
    await db_session.commit()
    assert collection_of(await stored(9_000_010)) is None


# ------------------------------------------------------------ statement budget
#
# The production ingest runs on a GitHub runner against hosted Postgres, ~150 ms
# away. Every statement is a round trip, so the number of statements *is* the
# run time. Written one title at a time, a title cost 57 statements when it was
# already stored and up to 98 when it was new (8.5 s each in production, which
# is why a full run could not finish in the job's 150 minutes). A batch now costs
# a fixed handful, however many titles, keywords and people it holds.

GENRES = [{"id": 18, "name": "Drama"}, {"id": 35, "name": "Comedy"}, {"id": 80, "name": "Crime"}]


class RichTmdb(FakeTmdb):
    """Payloads shaped like real ones: 25 keywords, 8 cast, the crew jobs ingest keeps."""

    async def get_genres(self, media_type: str) -> list[dict[str, Any]]:
        return GENRES

    def _movie(self, tmdb_id: int) -> dict[str, Any]:
        payload = super()._movie(tmdb_id)
        shared = [{"id": 1000 + i, "name": f"shared keyword {i}"} for i in range(10)]
        own = [{"id": tmdb_id * 100 + i, "name": f"keyword {tmdb_id}-{i}"} for i in range(15)]
        payload["genres"] = GENRES
        payload["keywords"] = {"keywords": shared + own}
        payload["credits"] = {
            "cast": [
                {"id": 5000 + (tmdb_id + i) % 12, "name": f"Actor {(tmdb_id + i) % 12}", "order": i}
                for i in range(8)
            ],
            "crew": [
                {"id": 6000 + tmdb_id % 3, "name": f"Director {tmdb_id % 3}", "job": "Director"},
                {"id": 7000 + tmdb_id, "name": f"Writer {tmdb_id}", "job": "Screenplay"},
                {"id": 7000 + tmdb_id, "name": f"Writer {tmdb_id}", "job": "Writer"},
                {"id": 8000 + tmdb_id, "name": f"Grip {tmdb_id}", "job": "Key Grip"},
            ],
        }
        return payload


async def _statements(work: Any) -> int:
    from app.core.timing import start_timer, stop_timer

    timer, token = start_timer()
    try:
        await work
    finally:
        stop_timer(token)
    return timer.db_statements


async def test_statements_per_batch_do_not_grow_with_titles(db_session: AsyncSession) -> None:
    service = CatalogIngestService(db_session, RichTmdb(), batch_size=20)  # type: ignore[arg-type]
    await service._ensure_genres()  # once per run, not per batch

    ten = [("movie", 9_200_000 + i) for i in range(10)]
    twenty = [("movie", 9_300_000 + i) for i in range(20)]
    new_10 = await _statements(service._ingest_ids(ten))
    new_20 = await _statements(service._ingest_ids(twenty))
    # A new run (fresh caches) over stored titles: the shape of a seed-only run.
    again = CatalogIngestService(db_session, RichTmdb(), batch_size=20)  # type: ignore[arg-type]
    await again._ensure_genres()
    refresh_10 = await _statements(again._ingest_ids(ten))
    assert (await db_session.scalar(select(func.count()).select_from(Credit))) > 0

    # SAVEPOINT; SELECT titles, people, keywords; INSERT people, keywords,
    # titles, title_genres, title_keywords, credits; RELEASE.
    assert new_10 == 11, f"{new_10} statements for a batch of new titles (pinned: 11)"
    assert new_20 == new_10, "a bigger batch must not cost more statements"
    # SAVEPOINT; SELECT x3; UPDATE titles (one executemany); DELETE x3 join
    # tables; INSERT x3 join tables; RELEASE. No people or keywords are new.
    assert refresh_10 == 12, f"{refresh_10} statements for a batch of stored titles (pinned: 12)"


async def test_a_row_the_database_rejects_costs_only_its_title(db_session: AsyncSession) -> None:
    """Postgres refuses one title's row (a value too long for its column).

    The set-based write fails as a whole, so the batch is written again one
    title at a time: the bad title fails alone, the rest are stored, and the
    bad title's other new people are rolled back with it, as before batching.
    """
    bad_id = 9_400_002

    class OneTooLong(RichTmdb):
        def _movie(self, tmdb_id: int) -> dict[str, Any]:
            payload = super()._movie(tmdb_id)
            if tmdb_id == bad_id:  # people.name is varchar(255), and names are never cut
                payload["credits"]["cast"][:0] = [
                    {"id": 9998, "name": "n" * 300, "order": 0},
                    {"id": 9999, "name": "Only Here", "order": 1},
                ]
            return payload

    service = CatalogIngestService(db_session, OneTooLong(), batch_size=10)  # type: ignore[arg-type]
    await service._ensure_genres()
    stats = await service._ingest_ids([("movie", 9_400_000 + i) for i in range(5)])

    assert stats == {"requested": 5, "created": 4, "updated": 0, "failed": 1}
    stored = set(await db_session.scalars(select(Title.external_tmdb_id)))
    assert {9_400_000 + i for i in range(5)} - stored == {bad_id}
    assert await db_session.scalar(select(Person.id).where(Person.external_tmdb_id == 9999)) is None


async def test_an_anthology_role_too_long_for_its_column_is_cut_not_the_film(
    db_session: AsyncSession,
) -> None:
    """TMDb 45005 lists every segment role in one 500-character string.

    credits.character is varchar(255), and the insert failing used to cost the
    whole film. The role is display text, so it is cut to fit with an ellipsis.
    """
    from app.application.catalog_ingest import CHARACTER_MAX_LENGTH

    role = " / ".join(f"Role {i} (segment \"Part {i}\")" for i in range(30))
    assert len(role) > CHARACTER_MAX_LENGTH

    class Anthology(RichTmdb):
        def _movie(self, tmdb_id: int) -> dict[str, Any]:
            payload = super()._movie(tmdb_id)
            payload["credits"]["cast"][0]["character"] = role
            return payload

    service = CatalogIngestService(db_session, Anthology(), batch_size=10)  # type: ignore[arg-type]
    await service._ensure_genres()
    stats = await service._ingest_ids([("movie", 9_450_000)])

    assert stats["created"] == 1 and stats["failed"] == 0
    stored = await db_session.scalar(
        select(Credit.character)
        .join(Title, Title.id == Credit.title_id)
        .where(Title.external_tmdb_id == 9_450_000, Credit.billing_order == 0)
    )
    assert stored is not None and len(stored) == CHARACTER_MAX_LENGTH
    assert stored.endswith("…") and role.startswith(stored[:-1])


class TrickyTmdb(RichTmdb):
    """Cross-title cases the batch has to resolve exactly as one-at-a-time did."""

    def _movie(self, tmdb_id: int) -> dict[str, Any]:
        payload = super()._movie(tmdb_id)
        n = tmdb_id % 10
        # Within the first MAX_CAST (8), or ingest never sees them:
        # the same person under another name in every title (the first name
        # seen is stored and used), and billed twice in one title (one credit).
        renamed = {"id": 5001, "name": f"Actor 1 as credited in {n}", "order": 0}
        payload["credits"]["cast"] = [
            renamed,
            dict(renamed, order=7),
            *payload["credits"]["cast"][:6],
        ]
        # A keyword TMDb renumbered: a new id under a name an earlier title stored.
        renamed = f"keyword {tmdb_id - 1}-0" if n else "renamed keyword"
        payload["keywords"]["keywords"].insert(0, {"id": 4000 + n, "name": renamed})
        # A genre missing from TMDb's genre list, and a TV-style genre name clash.
        payload["genres"] = [*GENRES, {"id": 10770 + n % 2, "name": "TV Movie"}]
        return payload


async def _catalog_rows(session: AsyncSession) -> dict[str, list[tuple[Any, ...]]]:
    """Everything ingest stores, keyed by TMDb ids and names instead of generated uuids."""
    from app.infrastructure.db.models.catalog import Genre, Keyword, TitleGenre, TitleKeyword

    session.expire_all()
    queries = {
        "titles": select(
            Title.external_tmdb_id, Title.media_type, Title.name, Title.runtime, Title.extra,
            Title.embedding,
        ),
        "genres": select(Genre.external_tmdb_id, Genre.name),
        "people": select(Person.external_tmdb_id, Person.name, Person.profile_path),
        "keywords": select(Keyword.external_tmdb_id, Keyword.name),
        "title_genres": select(Title.external_tmdb_id, Genre.name)
        .join(TitleGenre, TitleGenre.title_id == Title.id)
        .join(Genre, Genre.id == TitleGenre.genre_id),
        "title_keywords": select(Title.external_tmdb_id, Keyword.external_tmdb_id, Keyword.name)
        .join(TitleKeyword, TitleKeyword.title_id == Title.id)
        .join(Keyword, Keyword.id == TitleKeyword.keyword_id),
        "credits": select(
            Title.external_tmdb_id, Person.external_tmdb_id, Credit.credit_type, Credit.job,
            Credit.character, Credit.billing_order,
        )
        .join(Credit, Credit.title_id == Title.id)
        .join(Person, Person.id == Credit.person_id),
    }  # fmt: skip
    rows: dict[str, list[tuple[Any, ...]]] = {}
    for name, query in queries.items():
        result = (await session.execute(query)).all()
        rows[name] = sorted((tuple(map(_comparable, row)) for row in result), key=repr)
    return rows


def _comparable(value: Any) -> Any:
    if hasattr(value, "tolist"):  # pgvector hands back numpy arrays
        return tuple(value.tolist())
    if isinstance(value, dict):
        return repr(sorted(value.items()))
    return value


async def test_a_batch_stores_exactly_what_writing_one_title_at_a_time_did(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Batch size 1 is the old order of events: each title sees every earlier one stored.

    The batch must get there by itself. A mistake that breaks a statement
    would be repaired by the one-title-at-a-time fallback and still store the
    right rows, so the test also requires that the fallback never ran.
    """
    from app.infrastructure.db.base import Base
    from app.infrastructure.db.session import engine

    async def ingest(batch_size: int) -> dict[str, list[tuple[Any, ...]]]:
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        await db_session.commit()
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
        targets = [("movie", 9_500_100 + i) for i in range(8)]
        # A second run re-ingests some of them with nothing cached: lookups go
        # to the database (keywords by name included), not to this run's memory.
        for run_targets in (targets, targets[2:6]):
            service = CatalogIngestService(db_session, TrickyTmdb(), batch_size=batch_size)  # type: ignore[arg-type]
            await service._ensure_genres()
            assert (await service._ingest_ids(run_targets))["failed"] == 0
        return await _catalog_rows(db_session)

    one_at_a_time = await ingest(batch_size=1)
    batched = await ingest(batch_size=8)

    assert "ingest_batch_write_failed" not in caplog.text
    assert len(one_at_a_time["titles"]) == 8
    assert ("TV Movie",) in {row[1:] for row in one_at_a_time["genres"]}
    for table, rows in one_at_a_time.items():
        assert batched[table] == rows, table

    # And both are what ingest always stored: a stored person keeps the first
    # name seen, and a renumbered keyword links to the stored one instead of
    # getting a row of its own.
    assert dict((row[0], row[1]) for row in batched["people"])[5001] == "Actor 1 as credited in 0"
    keyword_ids = {row[0] for row in batched["keywords"]}
    assert 4000 in keyword_ids and keyword_ids.isdisjoint(range(4001, 4008))


async def test_reembed_rebuilds_the_features_ingest_stored(db_session: AsyncSession) -> None:
    """The database lists genres and keywords by name; ingest used TMDb's order.

    Re-embedding must not promote whichever genre sorts first (Action, here) to
    the main genre, nor keep the alphabetically first 20 keywords instead of the
    20 ingest kept.
    """
    from app.recommendation.pipeline import primary_genre
    from app.scripts import reembed_catalog

    class TmdbOrder(FakeTmdb):
        def _movie(self, tmdb_id: int) -> dict[str, Any]:
            payload = super()._movie(tmdb_id)
            payload["genres"] = [{"id": 53, "name": "Thriller"}, {"id": 28, "name": "Action"}]
            payload["keywords"] = {
                "keywords": [
                    {"id": 7_000 + i, "name": f"keyword {i:02d}"} for i in reversed(range(25))
                ]
            }
            return payload

    async def stored() -> Title:
        db_session.expire_all()
        title = await db_session.scalar(select(Title).where(Title.external_tmdb_id == 9_700_001))
        assert title is not None
        return title

    await CatalogIngestService(db_session, TmdbOrder(), batch_size=10).upsert_movie(9_700_001)  # type: ignore[arg-type]
    await db_session.commit()
    ingested = await stored()
    snapshot = dict(ingested.extra["feature_snapshot"])
    embedding = [float(x) for x in ingested.embedding]
    assert primary_genre(ingested) == "thriller"
    assert "kw:keyword 24" in snapshot and "kw:keyword 00" not in snapshot

    assert await reembed_catalog.main(limit=None, force=True) == 0

    rebuilt = await stored()
    assert rebuilt.extra["feature_snapshot"] == snapshot
    assert [float(x) for x in rebuilt.embedding] == pytest.approx(embedding, abs=1e-6)
