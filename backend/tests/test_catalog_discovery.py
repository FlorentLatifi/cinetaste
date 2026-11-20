"""Discovery passes: plan shape, walking, throttling, CLI flags (no network, no DB)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from app.application.catalog_discovery import (
    DEFAULT_LANGUAGES,
    DiscoveryPlan,
    Throttle,
    build_passes,
    collect_targets,
    max_discover_requests,
)
from app.application.catalog_ingest import CatalogIngestService
from app.core.config import Settings
from app.infrastructure.tmdb.client import TmdbClient
from app.scripts.ingest_catalog import build_parser, plan_from_args

# ------------------------------------------------------------------ the plan


def test_disabled_plan_is_the_legacy_mainstream_run() -> None:
    passes = build_passes(pages=10, include_tv=True)
    assert [(p.media_type, p.kwargs, p.pages, p.refresh) for p in passes] == [
        ("movie", {"sort_by": "popularity.desc"}, 10, True),
        ("movie", {"sort_by": "vote_count.desc"}, 10, True),
        ("tv", {"sort_by": "popularity.desc"}, 10, True),
        ("tv", {"sort_by": "vote_count.desc"}, 10, True),
    ]


def test_movies_only_has_no_tv_passes() -> None:
    passes = build_passes(pages=2, include_tv=False, plan=DiscoveryPlan())
    assert passes and {p.media_type for p in passes} == {"movie"}


def test_default_plan_passes_and_request_budget() -> None:
    passes = build_passes(pages=10, include_tv=True, plan=DiscoveryPlan())
    labels = [p.label for p in passes]

    # Mainstream first, so a run cut short keeps the most valuable titles.
    assert [p.refresh for p in passes[:4]] == [True] * 4
    assert not any(p.refresh for p in passes[4:])
    assert "movie:rated" in labels and "tv:rated" in labels
    assert {f"movie:lang:{c}" for c in DEFAULT_LANGUAGES} <= set(labels)
    assert [p.label for p in passes if ":decade:" in p.label] == [
        f"movie:decade:{d}s" for d in range(1940, 2030, 10)
    ]
    # 4*10 mainstream + 30 rated + 12*6 languages + 9*10 decades + 8 tv rated + 12*2 tv languages
    assert max_discover_requests(passes) == 264

    movie_titles = sum(p.pages for p in passes if p.media_type == "movie") * 20
    tv_titles = sum(p.pages for p in passes if p.media_type == "tv") * 20
    assert movie_titles == 4240  # raw ceiling before overlap; ~3.3-4k unique expected
    assert tv_titles < movie_titles / 2  # TV stays the smaller share


def test_discovery_passes_use_vote_floors_and_filters() -> None:
    by_label = {p.label: p for p in build_passes(pages=1, include_tv=True, plan=DiscoveryPlan())}
    assert by_label["movie:rated"].kwargs == {"sort_by": "vote_average.desc", "vote_count_gte": 250}
    ko = by_label["movie:lang:ko"].kwargs
    assert ko["original_language"] == "ko" and ko["vote_count_gte"] >= 100
    decade = by_label["movie:decade:1970s"].kwargs
    assert (decade["released_from"], decade["released_to"]) == ("1970-01-01", "1979-12-31")
    assert by_label["tv:lang:ko"].kwargs["original_language"] == "ko"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rated_pages": 501},
        {"decade_pages": -1},
        {"rated_min_votes": -5},
        {"languages": ("KO",)},
        {"languages": ("kor",)},
        {"decades": (1995,)},
        {"decades": (1850,)},
    ],
)
def test_plan_rejects_bad_values(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        DiscoveryPlan(**kwargs)


def test_zero_pages_switches_a_pass_off() -> None:
    plan = DiscoveryPlan(rated_pages=0, language_pages=0, decade_pages=0, tv_rated_pages=0, tv_language_pages=0)
    assert len(build_passes(pages=3, include_tv=True, plan=plan)) == 4


# ------------------------------------------------------------------- walking


class ScriptedTmdb:
    """discover() answers from a dict keyed by (media_type, sorted kwargs minus page)."""

    def __init__(self, pages: dict[str, list[list[int]]], fail: set[str] | None = None) -> None:
        self.pages = pages
        self.fail = fail or set()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def discover(self, media_type: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append((media_type, kwargs))
        key = f"{media_type}:{kwargs.get('original_language') or kwargs['sort_by']}"
        if key in self.fail:
            raise RuntimeError("boom")
        pages = self.pages.get(key, [])
        page = kwargs["page"]
        return [{"id": i} for i in pages[page - 1]] if page <= len(pages) else []


async def test_collect_dedupes_keeps_first_seen_order_and_reports_per_pass() -> None:
    tmdb = ScriptedTmdb(
        {
            "movie:popularity.desc": [[1, 2], [3]],
            "movie:vote_average.desc": [[2, 4], [5, 6]],
            "movie:ko": [[6, 7]],
        }
    )
    plan = DiscoveryPlan(
        rated_pages=5, languages=("ko",), language_pages=3, decades=(), tv_rated_pages=0, tv_language_pages=0
    )
    passes = build_passes(pages=5, include_tv=False, plan=plan)
    out = await collect_targets(tmdb, passes)

    assert list(out.items) == [("movie", i) for i in (1, 2, 3, 4, 5, 6, 7)]
    assert out.items[("movie", 1)] is True  # mainstream: refreshed on re-run
    assert out.items[("movie", 4)] is False  # discovery-only: skipped when stored
    # An empty page ends the pass early instead of walking all 5 requested pages.
    by = {r.label: r for r in out.reports}
    assert by["movie:rated"].pages_fetched == 2 and by["movie:rated"].new_unique == 3
    assert by["movie:lang:ko"].titles_seen == 2 and by["movie:lang:ko"].new_unique == 1
    assert out.discover_requests == len(tmdb.calls)


async def test_a_failing_pass_does_not_stop_the_others() -> None:
    tmdb = ScriptedTmdb(
        {"movie:popularity.desc": [[1]], "movie:ko": [[9]]},
        fail={"movie:vote_average.desc"},
    )
    plan = DiscoveryPlan(rated_pages=3, languages=("ko",), language_pages=1, decades=())
    out = await collect_targets(tmdb, build_passes(pages=1, include_tv=False, plan=plan))
    assert out.failed_passes == ["movie:rated"]
    assert ("movie", 9) in out.items and ("movie", 1) in out.items
    # It stopped at the failing page rather than retrying all 3.
    assert sum(1 for _, kw in tmdb.calls if kw.get("sort_by") == "vote_average.desc" and "original_language" not in kw) == 1


async def test_throttle_spaces_requests() -> None:
    throttle = Throttle(max_rps=100)  # 10 ms apart
    start = time.perf_counter()
    await asyncio.gather(*(throttle.wait() for _ in range(6)))
    assert time.perf_counter() - start >= 0.045

    start = time.perf_counter()
    await asyncio.gather(*(Throttle(0).wait() for _ in range(50)))
    assert time.perf_counter() - start < 0.05


# ------------------------------------------------------------- client params


def _client(handler: Any, monkeypatch: pytest.MonkeyPatch) -> TmdbClient:
    monkeypatch.setattr(TmdbClient, "_backoff", staticmethod(lambda attempt, retry_after: 0.0))
    settings = Settings(
        jwt_secret="unit-test-secret-key-at-least-32-chars!",
        database_url="postgresql+asyncpg://u:p@localhost/db",
        tmdb_api_key="test-key",
    )
    return TmdbClient(settings, transport=httpx.MockTransport(handler))


async def test_discover_sends_language_and_date_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"results": [{"id": 1}]})

    client = _client(handler, monkeypatch)
    await client.discover(
        "movie",
        page=2,
        sort_by="vote_average.desc",
        vote_count_gte=200,
        original_language="ko",
        released_from="1990-01-01",
        released_to="1999-12-31",
    )
    await client.discover("tv", released_from="2000-01-01", released_to="2009-12-31")
    await client.discover("movie")
    movie, tv, plain = (dict(u.params) for u in seen)
    assert movie["with_original_language"] == "ko" and movie["vote_count.gte"] == "200"
    assert movie["primary_release_date.gte"] == "1990-01-01"
    assert movie["primary_release_date.lte"] == "1999-12-31" and movie["page"] == "2"
    assert tv["first_air_date.gte"] == "2000-01-01" and "primary_release_date.gte" not in tv
    # Mainstream call is unchanged: no new parameters appear.
    assert set(plain) == {"api_key", "page", "sort_by", "vote_count.gte", "include_adult"}
    await client.aclose()


async def test_discover_rejects_pages_tmdb_would_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(lambda r: httpx.Response(200, json={}), monkeypatch)
    with pytest.raises(ValueError):
        await client.discover("movie", page=501)
    with pytest.raises(ValueError):
        await client.discover("movie", page=0)
    with pytest.raises(ValueError):
        await client.discover("person")
    await client.aclose()


# ----------------------------------------------------------------------- CLI


def test_cli_defaults_equal_the_default_plan() -> None:
    args = build_parser().parse_args([])
    assert plan_from_args(args) == DiscoveryPlan()
    assert args.max_rps == 25.0 and not args.refresh_existing and not args.dry_run


def test_cli_flags_override_and_none_clears_lists() -> None:
    args = build_parser().parse_args(
        ["--languages", "ko,ja", "--decades", "none", "--rated-pages", "0", "--language-pages", "3"]
    )
    plan = plan_from_args(args)
    assert plan.languages == ("ko", "ja") and plan.decades == () and plan.rated_pages == 0
    assert plan.language_pages == 3


def test_cli_no_discovery_is_mainstream_only() -> None:
    plan = plan_from_args(build_parser().parse_args(["--no-discovery"]))
    assert len(build_passes(pages=10, include_tv=True, plan=plan)) == 4


@pytest.mark.parametrize(
    "argv", [["--languages", "korean"], ["--decades", "nineties"], ["--decades", "1995"], ["--rated-pages", "900"]]
)
def test_cli_rejects_bad_values(argv: list[str]) -> None:
    with pytest.raises(ValueError):
        plan_from_args(build_parser().parse_args(argv))


# ------------------------------------------------- skip-existing (fake session)


class _Rows:
    def __init__(self, rows: list[tuple[int, str]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[int, str]]:
        return self._rows


class StoredSession:
    def __init__(self, stored: dict[int, str]) -> None:
        self.stored = stored
        self.queries = 0

    async def execute(self, stmt: Any) -> _Rows:
        self.queries += 1
        return _Rows(list(self.stored.items()))


async def test_plan_fetch_skips_stored_discovery_titles_but_refreshes_mainstream() -> None:
    session = StoredSession({1: "movie", 2: "movie", 3: "movie", 50: "movie"})
    service = CatalogIngestService(session, object())  # type: ignore[arg-type]
    items = {
        ("movie", 1): True,  # mainstream, stored -> refreshed
        ("movie", 2): False,  # discovery, stored -> skipped
        ("movie", 4): False,  # discovery, new -> fetched
        ("tv", 50): False,  # same id stored as a movie -> collision, never fetched
    }
    to_fetch, skipped, collisions = await service._plan_fetch(items, refresh_existing=False)
    assert to_fetch == [("movie", 1), ("movie", 4)]
    assert (skipped, collisions) == (1, 1)

    to_fetch, skipped, collisions = await service._plan_fetch(items, refresh_existing=True)
    assert len(to_fetch) == 4 and (skipped, collisions) == (0, 0)
    assert session.queries == 1  # refresh_existing never touches the database
