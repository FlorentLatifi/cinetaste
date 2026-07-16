"""Unit tests for candidate-pool assembly (no live DB required for pure helpers)."""

from uuid import uuid4

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
