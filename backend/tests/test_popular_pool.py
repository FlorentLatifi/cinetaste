"""The shared popularity slice: loaded once per TTL, in its own session."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

from app.application.popular_pool import PopularPool, get_popular_pool, reset_popular_pool


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Factory:
    """Stands in for async_session_factory: counts sessions, records what was asked."""

    def __init__(self, rows: list[object]) -> None:
        self.rows = rows
        self.sessions = 0
        self.limits: list[int | None] = []

    @asynccontextmanager
    async def __call__(self):  # noqa: ANN204
        self.sessions += 1
        factory = self

        class _Session:
            async def scalars(self, stmt):  # noqa: ANN001, ANN202
                factory.limits.append(stmt._limit)
                return SimpleNamespace(all=lambda: list(factory.rows))

        yield _Session()


async def test_second_call_within_ttl_does_not_touch_the_database() -> None:
    factory = _Factory(["a", "b"])
    pool = PopularPool(factory, ttl_seconds=300, clock=_Clock())
    assert await pool.get(120) == ("a", "b")
    assert await pool.get(120) == ("a", "b")
    assert factory.sessions == 1 and pool.loads == 1
    assert factory.limits == [120]


async def test_reloads_after_the_ttl() -> None:
    clock, factory = _Clock(), _Factory(["a"])
    pool = PopularPool(factory, ttl_seconds=300, clock=clock)
    await pool.get(120)
    clock.now += 299
    await pool.get(120)
    assert pool.loads == 1
    clock.now += 2
    factory.rows = ["a", "new"]
    assert await pool.get(120) == ("a", "new")
    assert pool.loads == 2


async def test_a_different_limit_is_a_different_pool() -> None:
    factory = _Factory(["a"])
    pool = PopularPool(factory, ttl_seconds=300, clock=_Clock())
    await pool.get(120)
    await pool.get(40)
    assert factory.limits == [120, 40]


async def test_an_empty_result_is_not_remembered() -> None:
    """An empty catalog is usually "ingest has not finished", not a fact to keep."""
    factory = _Factory([])
    pool = PopularPool(factory, ttl_seconds=300, clock=_Clock())
    assert await pool.get(120) == ()
    factory.rows = ["a"]
    assert await pool.get(120) == ("a",)
    assert pool.loads == 2


async def test_clear_forces_a_reload() -> None:
    factory = _Factory(["a"])
    pool = PopularPool(factory, ttl_seconds=300, clock=_Clock())
    await pool.get(120)
    pool.clear()
    await pool.get(120)
    assert pool.loads == 2


def test_ttl_zero_means_no_shared_pool() -> None:
    reset_popular_pool()
    assert get_popular_pool(0) is None
    assert get_popular_pool(-1) is None


def test_shared_pool_is_a_singleton_until_reset() -> None:
    reset_popular_pool()
    first = get_popular_pool(300)
    assert first is not None and get_popular_pool(300) is first
    reset_popular_pool()
    assert get_popular_pool(300) is not first
    reset_popular_pool()
