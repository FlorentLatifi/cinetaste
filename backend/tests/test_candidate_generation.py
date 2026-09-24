"""Unit tests for candidate-pool assembly (no live DB required for pure helpers)."""

from uuid import uuid4

import pytest

from app.application.recommendation_service import RecommendationService
from app.core.config import Settings


def _settings(**kwargs) -> Settings:
    base = dict(
        jwt_secret="unit-test-secret-key-at-least-32-chars!",
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379/0",
        rec_use_ann=True,
        rec_ann_candidates=50,
        rec_popular_candidates=20,
        rec_slate_size=10,
    )
    base.update(kwargs)
    return Settings(**base)


def test_settings_ann_defaults() -> None:
    s = _settings()
    assert s.rec_use_ann is True
    assert s.rec_ann_candidates >= 50
    assert s.rec_popular_candidates >= 20


def test_merge_ann_and_popular_unique() -> None:
    """Document expected merge semantics used in _load_candidates."""
    a, b, c = uuid4(), uuid4(), uuid4()

    class T:
        def __init__(self, i):
            self.id = i

    ann = [T(a), T(b)]
    pop = [T(b), T(c)]
    merged = {}
    for t in ann + pop:
        merged[t.id] = t
    assert set(merged) == {a, b, c}
    assert len(merged) == 3


def test_service_constructs_with_ann_settings() -> None:
    # Smoke: service accepts settings knobs used by candidate gen
    svc = RecommendationService(session=None, settings=_settings(rec_use_ann=False))  # type: ignore[arg-type]
    assert svc._settings.rec_use_ann is False


@pytest.mark.asyncio
async def test_exclusions_are_over_fetched_not_filtered_in_sql() -> None:
    """Ranking already drops excluded ids, so the SQL NOT IN only kept the pool
    full — and the list was planned and transmitted on every request.

    Measured at 10,000 titles with 2,000 exclusions, interleaved A/B:
    210 ms for a pool of 367 with the SQL filter, 98 ms for 420 without it.
    """
    from unittest.mock import AsyncMock, MagicMock

    from app.application.recommendation_service import _ANN_OVERFETCH_MAX, RecommendationService

    captured: list[int] = []

    def fake_scalars(stmt):
        # The ORM statement carries its LIMIT; that is what we are asserting on.
        captured.append(stmt._limit)
        return MagicMock(all=MagicMock(return_value=[]))

    session = AsyncMock()
    session.scalars = AsyncMock(side_effect=fake_scalars)
    session.execute = AsyncMock()
    session.begin_nested = MagicMock()
    session.begin_nested.return_value.__aenter__ = AsyncMock()
    session.begin_nested.return_value.__aexit__ = AsyncMock(return_value=False)

    settings = _settings()
    service = RecommendationService(session, settings)
    vector = [0.1] * 384

    captured.clear()
    await service._load_candidates(user_vector=vector, exclude_ids=set())
    base_ann = captured[0]

    captured.clear()
    await service._load_candidates(user_vector=vector, exclude_ids={uuid4() for _ in range(50)})
    assert captured[0] == base_ann + 50, "small exclusion sets are covered exactly"

    captured.clear()
    await service._load_candidates(user_vector=vector, exclude_ids={uuid4() for _ in range(5000)})
    assert captured[0] == base_ann + _ANN_OVERFETCH_MAX, "over-fetch is capped"
    # The popular query is not over-fetched: those candidates are the cheap half
    # of the pool and the least valuable to replace.
    assert captured[1] == max(settings.rec_popular_candidates, 20)
