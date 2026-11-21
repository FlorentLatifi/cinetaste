"""The offline-evaluation TMDb cache must build features exactly as ingest does.

If the evaluation computed title features differently from ingest, its numbers
would describe a recommender nobody runs. The parity tests feed the same TMDb
payload to the real ``CatalogIngestService._upsert_from_detail`` (on a fake
session) and to ``signal_inputs_from_payload`` → ``build_title_signals``, and
require identical feature snapshots and content vectors.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.application.catalog_ingest import (
    MAX_CAST,
    MAX_KEYWORDS,
    CatalogIngestService,
    tmdb_collection,
)
from app.domain.exceptions import AppError
from app.infrastructure.db.models.catalog import Title
from app.recommendation.embeddings import build_title_signals
from app.recommendation.tmdb_eval_cache import (
    REPO_ROOT,
    STATUS_NOT_FOUND,
    STATUS_OK,
    ensure_outside_repo,
    fetch_into_cache,
    load_cache,
    read_movielens_links,
    signal_inputs_from_payload,
    trim_movie_payload,
)


class _RecordingSession:
    """Enough AsyncSession for ``_upsert_from_detail`` on an empty database."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    async def scalar(self, _statement: Any) -> None:
        return None

    async def scalars(self, _statement: Any) -> list[Any]:
        return []  # an empty database: every lookup finds no rows

    async def execute(self, _statement: Any) -> None:
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


def _ingest(media_type: str, payload: dict[str, Any]) -> Title:
    session = _RecordingSession()
    service = CatalogIngestService(session, tmdb=None)  # type: ignore[arg-type]
    asyncio.run(service._upsert_from_detail(media_type, payload))
    titles = [obj for obj in session.added if isinstance(obj, Title)]
    assert len(titles) == 1
    return titles[0]


def _movie_payload() -> dict[str, Any]:
    cast = [{"id": 100 + i, "name": f"Actor {i}", "order": i} for i in range(MAX_CAST + 6)]
    cast[2] = {"id": 102, "name": "", "order": 2}  # skipped after the MAX_CAST cut
    cast[4] = {"id": 101, "name": "Actor 1", "order": 4}  # same person billed twice
    return {
        "id": 550,
        "imdb_id": "tt0137523",
        "title": "Fight Club",
        "original_title": "Fight Club",
        "overview": "A ticking-time-bomb insomniac and a slippery soap salesman channel "
        "primal male aggression into a shocking new form of therapy.",
        "release_date": "1999-10-15",
        "runtime": 139,
        "original_language": "en",
        "popularity": 31.4,
        "vote_average": 8.4,
        "vote_count": 31000,
        "tagline": "Mischief. Mayhem. Soap.",  # not read; trimmed away
        "genres": [{"id": 18, "name": "Drama"}, {"id": 53, "name": "Thriller"}],
        "keywords": {
            "keywords": [{"id": 900 + i, "name": f"keyword {i}"} for i in range(MAX_KEYWORDS + 5)]
            + [{"id": 1, "name": "dual identity"}, {"id": 2, "name": "neo-noir"}]
        },
        "production_countries": [{"iso_3166_1": "US"}, {"iso_3166_1": "de"}],
        "credits": {
            "cast": cast,
            "crew": [
                {"id": 7467, "name": "David Fincher", "job": "Director"},
                {"id": 7468, "name": "Chuck Palahniuk", "job": "Novel"},
                {"id": 7469, "name": "Jim Uhls", "job": "Screenplay"},
                {"id": 7469, "name": "Jim Uhls", "job": "Screenplay"},  # duplicate credit
                {"id": 7469, "name": "Jim Uhls", "job": "Writer"},  # same person, other job
                {"id": 7470, "name": "Jeff Cronenweth", "job": "Director of Photography"},
                {"id": 7471, "name": "Nobody", "job": "Gaffer"},
                {"id": None, "name": "No Id", "job": "Director"},
            ],
        },
    }


def _signals(media_type: str, payload: dict[str, Any]) -> tuple[list[float], dict[str, Any]]:
    embedding, _features, meta = build_title_signals(**signal_inputs_from_payload(media_type, payload))
    return embedding, meta


def test_movie_features_match_ingest_exactly() -> None:
    payload = _movie_payload()
    title = _ingest("movie", payload)
    embedding, meta = _signals("movie", trim_movie_payload(payload))

    assert meta == title.extra
    assert embedding == pytest.approx(title.embedding, abs=1e-12)
    snapshot = meta["feature_snapshot"]
    assert "person:director:david fincher" in snapshot
    assert "person:writer:jim uhls" in snapshot
    assert "person:writer:chuck palahniuk" not in snapshot  # "Novel" is not an ingest job
    assert "country:DE" in snapshot


def test_collection_survives_trimming_and_matches_ingest() -> None:
    """The franchise cap reads ``extra["collection"]``; evaluation must see what ingest stores."""
    payload = {
        **_movie_payload(),
        "belongs_to_collection": {"id": 10, "name": " Fight Saga ", "poster_path": "/x.jpg"},
    }
    title = _ingest("movie", payload)
    trimmed = trim_movie_payload(payload)
    assert trimmed["belongs_to_collection"] == {"id": 10, "name": " Fight Saga "}
    assert tmdb_collection(trimmed["belongs_to_collection"]) == title.extra["collection"]
    assert title.extra["collection"] == {"id": 10, "name": "Fight Saga"}
    standalone = trim_movie_payload({**_movie_payload(), "belongs_to_collection": None})
    assert standalone["belongs_to_collection"] is None


def test_untrimmed_and_trimmed_payloads_give_the_same_signals() -> None:
    payload = _movie_payload()
    assert _signals("movie", payload) == _signals("movie", trim_movie_payload(payload))


def test_tv_features_match_ingest_exactly() -> None:
    payload = {
        "id": 1396,
        "name": "Breaking Bad",
        "original_name": "Breaking Bad",
        "overview": "A chemistry teacher diagnosed with cancer turns to crime.",
        "first_air_date": "2008-01-20",
        "episode_run_time": [47],
        "original_language": "en",
        "genres": [{"id": 18, "name": "Drama"}, {"id": 80, "name": "Crime"}],
        "keywords": {"results": [{"id": 1, "name": "drug dealer"}, {"id": 2, "name": "neo-noir"}]},
        "production_countries": [],
        "origin_country": ["US"],
        "created_by": [{"id": 66633, "name": "Vince Gilligan"}],
        "credits": {"cast": [{"id": 17419, "name": "Bryan Cranston", "order": 0}], "crew": []},
    }
    title = _ingest("tv", payload)
    embedding, meta = _signals("tv", payload)
    assert meta == title.extra
    assert embedding == pytest.approx(title.embedding, abs=1e-12)
    assert "person:writer:vince gilligan" in meta["feature_snapshot"]


def test_runtime_zero_is_kept_as_ingest_keeps_it() -> None:
    """TMDb reports unknown runtimes as 0; ingest buckets that as "short"."""
    payload = _movie_payload() | {"runtime": 0}
    assert _signals("movie", payload)[1] == _ingest("movie", payload).extra


def test_cache_paths_inside_the_repository_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inside the repository"):
        ensure_outside_repo(REPO_ROOT / "data" / "tmdb.jsonl")
    assert ensure_outside_repo(tmp_path / "tmdb.jsonl") == (tmp_path / "tmdb.jsonl").resolve()


class _FakeTmdb:
    def __init__(self, *, rate_limit_after: int | None = None) -> None:
        self.calls: list[int] = []
        self._rate_limit_after = rate_limit_after

    async def get_movie(self, tmdb_id: int) -> dict[str, Any]:
        self.calls.append(tmdb_id)
        if self._rate_limit_after is not None and len(self.calls) > self._rate_limit_after:
            raise AppError("TMDB rate limit exceeded", status_code=503, code="tmdb_rate_limited")
        if tmdb_id == 404:
            request = httpx.Request("GET", f"https://api.themoviedb.org/3/movie/{tmdb_id}")
            raise httpx.HTTPStatusError(
                "not found", request=request, response=httpx.Response(404, request=request)
            )
        return _movie_payload() | {"id": tmdb_id}


def test_fetch_appends_records_and_resumes(tmp_path: Path) -> None:
    cache = tmp_path / "tmdb.jsonl"
    first = asyncio.run(fetch_into_cache(_FakeTmdb(), [1, 2, 404], cache, max_rps=0))
    assert (first.fetched, first.not_found, first.failed) == (2, 1, 0)

    records = load_cache(cache)
    assert records[1]["status"] == STATUS_OK
    assert records[404]["status"] == STATUS_NOT_FOUND
    assert "tagline" not in records[1]["payload"]  # trimmed

    client = _FakeTmdb()
    second = asyncio.run(fetch_into_cache(client, [1, 2, 3, 404], cache, max_rps=0))
    assert client.calls == [3]  # cached ids, found or not, are never re-requested
    assert second.already_cached == 3 and second.fetched == 1


def test_fetch_stops_on_rate_limit_and_keeps_progress(tmp_path: Path) -> None:
    cache = tmp_path / "tmdb.jsonl"
    report = asyncio.run(
        fetch_into_cache(
            _FakeTmdb(rate_limit_after=2), range(1, 50), cache, max_rps=0, concurrency=1
        )
    )
    assert report.stopped_reason == "tmdb_rate_limited"
    assert report.fetched == 2
    assert len(load_cache(cache)) == 2


def test_torn_last_line_is_ignored(tmp_path: Path) -> None:
    cache = tmp_path / "tmdb.jsonl"
    cache.write_text(
        json.dumps({"tmdb_id": 5, "status": STATUS_NOT_FOUND}) + "\n" + '{"tmdb_id": 6, "sta',
        encoding="utf-8",
    )
    assert list(load_cache(cache)) == [5]


def test_movielens_links_skip_missing_tmdb_ids(tmp_path: Path) -> None:
    (tmp_path / "links.csv").write_text(
        "movieId,imdbId,tmdbId\n1,0114709,862\n2,0113497,\n3,0113228,15602\n", encoding="utf-8"
    )
    assert read_movielens_links(tmp_path) == {"1": 862, "3": 15602}
