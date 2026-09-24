"""What happens when the catalog was ingested but never embedded.

Deploying is two steps — `ingest_catalog`, then `reembed_catalog` — and ranking
drops every title whose embedding is NULL. Skip the second step and every user
gets an empty For You with a 200 and no error anywhere. These tests pin the
behaviour and the signals that now make it visible.
"""

from __future__ import annotations

import logging
from uuid import UUID

import pytest

from app.recommendation.pipeline import RankingWeights, rank_titles
from tests.conftest import FakeTitle

# Never leaves the mocked session.
SOME_USER = UUID("33333333-3333-4333-8333-333333333333")


def _catalog(n: int = 8) -> list[FakeTitle]:
    return [
        FakeTitle(name=f"Film {i}", genres=["Drama"], keywords=["k"], popularity=10 + i)
        for i in range(n)
    ]


def _rank(titles, *, user_vector, features=None, slate_size=5):
    return rank_titles(
        user_vector=user_vector,
        user_features=features if features is not None else {"genre:drama": 1.0},
        titles=titles,
        exclude_ids=set(),
        slate_size=slate_size,
        mmr_lambda=0.8,
        weights=RankingWeights(),
    )


def test_a_fully_unembedded_catalog_ranks_nothing() -> None:
    """The failure this whole module exists for.

    Not an exception, not a 500 — an empty list, which the API happily returns
    as a 200 with zero items.
    """
    titles = _catalog()
    vector = list(titles[0].embedding)
    for title in titles:
        object.__setattr__(title, "embedding", None)

    assert _rank(titles, user_vector=vector) == []


def test_partially_embedded_catalog_still_ranks_the_rest() -> None:
    titles = _catalog()
    vector = list(titles[0].embedding)
    for title in titles[:3]:
        object.__setattr__(title, "embedding", None)

    ranked = _rank(titles, user_vector=vector)
    assert len(ranked) == 5
    unembedded = {t.id for t in titles[:3]}
    assert {item.title_id for item in ranked}.isdisjoint(unembedded)


@pytest.mark.parametrize(
    "broken",
    [pytest.param([0.1, 0.2, 0.3], id="wrong dimensionality"), pytest.param([], id="empty vector")],
)
def test_malformed_vectors_do_not_crash_the_ranker(broken) -> None:
    """A half-finished re-embed can leave rows like these behind."""
    titles = _catalog()
    vector = list(titles[0].embedding)
    object.__setattr__(titles[0], "embedding", broken)

    ranked = _rank(titles, user_vector=vector)
    assert len(ranked) == 5


def test_a_malformed_user_vector_falls_back_to_features() -> None:
    """Similarity is skipped, sparse features still rank."""
    ranked = _rank(_catalog(), user_vector=[0.5, 0.5])
    assert len(ranked) == 5


def test_empty_candidate_pool_is_empty_not_an_error() -> None:
    assert _rank([], user_vector=None) == []


@pytest.mark.asyncio
async def test_for_you_warns_when_no_candidate_is_embedded(caplog) -> None:
    """The runtime signal: a 200 with zero items has to say something somewhere."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.application.recommendation_service import RecommendationService
    from app.core.config import Settings

    settings = Settings(
        jwt_secret="unit-test-secret-key-at-least-32-chars!",
        database_url="postgresql+asyncpg://u:p@localhost/x",
        rec_log_impressions=False,
    )
    titles = _catalog()
    for title in titles:
        object.__setattr__(title, "embedding", None)

    session = AsyncMock()
    session.get = AsyncMock(return_value=None)  # no taste profile yet
    session.scalars = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[])))

    service = RecommendationService(session, settings)
    with (
        patch.object(service, "_load_candidates", AsyncMock(return_value=titles)),
        caplog.at_level(logging.WARNING, logger="app.application.recommendation_service"),
    ):
        slate = await service.for_you(SOME_USER)

    assert slate.items == []
    # getMessage(), not .message: the latter is only populated once a
    # formatter has run, which caplog does not guarantee.
    assert any("slate_candidates_unembedded" in r.getMessage() for r in caplog.records)
    assert "reembed_catalog" in caplog.text  # the warning has to say what to do
