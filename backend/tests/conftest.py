"""Shared fixtures and fakes for recommendation / taste unit tests.

No database required — pure ranking and service-mock tests only.

This module also redirects the whole session at a disposable database, before
anything imports the app. It has to happen here: ``app.infrastructure.db.session``
builds its engine at import time, and pytest loads this file before any test
module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest


def _redirect_to_test_database() -> None:
    """Point the session at ``<name>_test`` unless it already is one.

    The integration suite TRUNCATEs every table. The configured URL is the same
    one the dev server uses, so running it locally erased whatever you were
    working on. Deriving the name here makes the safe thing the default; the
    guard in ``tests/integration/conftest.py`` is the backstop for anyone who
    overrides DATABASE_URL by hand.

    Resolved through Settings rather than os.environ because the URL usually
    comes from .env, and then written *back* to os.environ so that subprocesses
    — alembic, in particular — inherit it.
    """
    from app.core.config import get_settings

    url = get_settings().database_url
    base, sep, query = url.partition("?")
    if base.rstrip("/").endswith("_test"):
        return

    os.environ["DATABASE_URL"] = f"{base.rstrip('/')}_test{sep}{query}"
    # Settings is lru_cached and the engine is built from it at import time.
    get_settings.cache_clear()


_redirect_to_test_database()

# Imported after the redirect on purpose: anything under `app` may pull in the
# database session, which builds its engine from Settings at import time.
from app.recommendation.embeddings import (  # noqa: E402
    PersonSignal,
    build_title_embedding,
    features_from_title,
)


@dataclass
class FakeGenre:
    name: str


@dataclass
class FakeTitle:
    """Minimal stand-in for catalog.Title used by rank_titles."""

    name: str
    genres: list[str]
    keywords: list[str] = field(default_factory=list)
    people: list[PersonSignal | tuple[str, str] | str] = field(default_factory=list)
    release_year: int = 2018
    runtime: int = 110
    popularity: float = 30.0
    vote_average: float = 7.5
    media_type: str = "movie"
    original_language: str = "en"
    countries: list[str] = field(default_factory=lambda: ["US"])
    overview: str = "A film."
    id: UUID = field(default_factory=uuid4)
    embedding: list[float] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        genre_objs = [FakeGenre(g) for g in self.genres]
        # Store as objects with .name for pipeline genre cap
        object.__setattr__(self, "_genre_names", list(self.genres))
        object.__setattr__(self, "genres", genre_objs)
        snap = features_from_title(
            genres=self._genre_names,
            keywords=self.keywords,
            people=self.people,
            release_year=self.release_year,
            runtime=self.runtime,
            media_type=self.media_type,
            original_language=self.original_language,
            countries=self.countries,
        )
        emb = build_title_embedding(
            name=self.name,
            overview=self.overview,
            genres=self._genre_names,
            keywords=self.keywords,
            people=self.people,
            media_type=self.media_type,
            release_year=self.release_year,
            runtime=self.runtime,
            original_language=self.original_language,
            countries=self.countries,
        )
        if self.embedding is None:
            object.__setattr__(self, "embedding", emb)
        if not self.extra:
            object.__setattr__(
                self,
                "extra",
                {"feature_snapshot": snap, "feature_schema_version": 2},
            )


def accumulate_features(
    events: list[tuple[dict[str, float], float, str]],
) -> dict[str, float]:
    """Simulate taste recompute sparse accumulation (policy weights already applied)."""
    from app.domain.taste_signals import affects_taste

    acc: dict[str, float] = {}
    for snap, weight, event_type in events:
        if not affects_taste(event_type, weight):
            continue
        for key, value in snap.items():
            if str(key).startswith("__"):
                continue
            acc[str(key)] = acc.get(str(key), 0.0) + float(value) * float(weight)
    return {k: v for k, v in acc.items() if abs(v) > 0.05}


@pytest.fixture(autouse=True)
def isolated_cache(request: pytest.FixtureRequest):
    """Give every unit test its own empty cache and rate-limit store.

    Otherwise they share whatever REDIS_URL points at, and a slate cached by an
    earlier run is served to a later one — which is how a test asserting an
    empty For You quietly passed for the wrong reason, then failed when the log
    it was actually checking never appeared.

    Integration tests are left alone: they exercise the real store on purpose
    and clear it through their own fixture.
    """
    if request.node.get_closest_marker("integration"):
        yield
        return

    from app.infrastructure import cache

    previous = (cache._store, cache._rate_limit_store)
    cache._store = cache.MemoryStore()
    cache._rate_limit_store = cache.MemoryStore()
    try:
        yield
    finally:
        cache._store, cache._rate_limit_store = previous


@pytest.fixture
def thriller_catalog() -> list[FakeTitle]:
    """Small diverse catalog for ranking tests."""
    return [
        FakeTitle(
            name="Neon Noir",
            genres=["Thriller", "Crime"],
            keywords=["neo-noir", "detective"],
            people=[PersonSignal("Ava Voss", "director")],
            popularity=25,
            vote_average=8.0,
            overview="Rain-soaked detective story.",
        ),
        FakeTitle(
            name="Laugh Factory",
            genres=["Comedy"],
            keywords=["feel-good", "wedding"],
            people=[PersonSignal("Sam Jokes", "director")],
            popularity=90,
            vote_average=6.5,
            overview="Broad romantic comedy.",
        ),
        FakeTitle(
            name="Quiet Stars",
            genres=["Science Fiction", "Drama"],
            keywords=["time travel", "mind-bending"],
            people=[PersonSignal("Chris Nolan", "director")],
            popularity=40,
            vote_average=8.2,
            overview="Cerebral space and time.",
        ),
        FakeTitle(
            name="Slapstick City",
            genres=["Comedy"],
            keywords=["slapstick"],
            people=[PersonSignal("Sam Jokes", "director")],
            popularity=85,
            vote_average=6.2,
            overview="More comedy from the same director.",
        ),
        FakeTitle(
            name="Harbor Crime",
            genres=["Thriller", "Crime"],
            keywords=["detective"],
            people=[PersonSignal("Ava Voss", "director")],
            popularity=22,
            vote_average=7.6,
            overview="Another noir thriller.",
        ),
        FakeTitle(
            name="Indie Gem",
            genres=["Drama"],
            keywords=["melancholy"],
            people=[PersonSignal("Lee Park", "director")],
            popularity=12,
            vote_average=7.8,
            overview="Quiet character study.",
        ),
    ]
