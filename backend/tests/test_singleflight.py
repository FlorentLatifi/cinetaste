"""Single-flight: one computation per cold key, and failure is never shared."""

from __future__ import annotations

import asyncio

import pytest

from app.core.singleflight import SingleFlight


class Cache:
    """A cache whose lookup is separate from compute, like the route's store."""

    def __init__(self) -> None:
        self.value: str | None = None
        self.computes = 0

    async def lookup(self) -> str | None:
        return self.value


def _compute(cache: Cache, *, delay: float = 0.05, result: str = "deck", store: bool = True):
    async def compute() -> str:
        cache.computes += 1
        await asyncio.sleep(delay)
        if store:
            cache.value = result
        return result

    return compute


async def test_concurrent_misses_compute_once_and_share_the_result() -> None:
    flight, cache = SingleFlight[str](), Cache()
    results = await asyncio.gather(
        *(
            flight.do("k", lookup=cache.lookup, compute=_compute(cache), wait_seconds=5)
            for _ in range(25)
        )
    )
    assert cache.computes == 1
    assert {value for value, _how in results} == {"deck"}
    assert sorted(how for _v, how in results) == ["computed"] + ["shared"] * 24
    assert flight.in_flight == 0, "nothing is left registered"


async def test_followers_do_not_need_the_cache_to_work() -> None:
    """If the store is down the leader's result is still handed over directly."""
    flight, cache = SingleFlight[str](), Cache()
    results = await asyncio.gather(
        *(
            flight.do("k", lookup=cache.lookup, compute=_compute(cache, store=False), wait_seconds=5)
            for _ in range(10)
        )
    )
    assert cache.computes == 1
    assert [value for value, _ in results] == ["deck"] * 10


async def test_a_warm_cache_never_computes() -> None:
    flight, cache = SingleFlight[str](), Cache()
    cache.value = "warm"
    value, how = await flight.do("k", lookup=cache.lookup, compute=_compute(cache), wait_seconds=5)
    assert (value, how, cache.computes) == ("warm", "hit", 0)


async def test_later_callers_hit_the_cache_not_a_new_flight() -> None:
    flight, cache = SingleFlight[str](), Cache()
    await flight.do("k", lookup=cache.lookup, compute=_compute(cache), wait_seconds=5)
    _value, how = await flight.do("k", lookup=cache.lookup, compute=_compute(cache), wait_seconds=5)
    assert how == "hit" and cache.computes == 1


async def test_different_keys_are_independent() -> None:
    flight, cache_a, cache_b = SingleFlight[str](), Cache(), Cache()
    await asyncio.gather(
        flight.do("a", lookup=cache_a.lookup, compute=_compute(cache_a), wait_seconds=5),
        flight.do("b", lookup=cache_b.lookup, compute=_compute(cache_b, result="other"), wait_seconds=5),
    )
    assert (cache_a.computes, cache_b.computes) == (1, 1)


async def test_a_failing_leader_fails_alone_and_followers_do_the_work_themselves() -> None:
    flight, cache = SingleFlight[str](), Cache()
    calls = 0

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.03)
        if calls == 1:
            raise RuntimeError("db hiccup")
        return "deck"

    results = await asyncio.gather(
        *(flight.do("k", lookup=cache.lookup, compute=flaky, wait_seconds=5) for _ in range(5)),
        return_exceptions=True,
    )
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], RuntimeError), "only the leader sees its error"
    followers = [r for r in results if not isinstance(r, Exception)]
    assert [value for value, _ in followers] == ["deck"] * 4
    assert {how for _v, how in followers} == {"fallback"}
    assert flight.in_flight == 0


async def test_a_cancelled_leader_does_not_cancel_or_poison_followers() -> None:
    flight, cache = SingleFlight[str](), Cache()
    started = asyncio.Event()

    async def slow() -> str:
        started.set()
        await asyncio.sleep(30)
        return "never"

    leader = asyncio.create_task(flight.do("k", lookup=cache.lookup, compute=slow, wait_seconds=5))
    await started.wait()
    follower = asyncio.create_task(
        flight.do("k", lookup=cache.lookup, compute=_compute(cache), wait_seconds=5)
    )
    await asyncio.sleep(0.02)  # the follower is now waiting on the leader
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader

    value, how = await asyncio.wait_for(follower, timeout=2)
    assert (value, how) == ("deck", "fallback")
    assert flight.in_flight == 0


async def test_a_cancelled_follower_does_not_disturb_the_leader() -> None:
    flight, cache = SingleFlight[str](), Cache()
    leader = asyncio.create_task(
        flight.do("k", lookup=cache.lookup, compute=_compute(cache, delay=0.1), wait_seconds=5)
    )
    await asyncio.sleep(0.01)
    follower = asyncio.create_task(
        flight.do("k", lookup=cache.lookup, compute=_compute(cache), wait_seconds=5)
    )
    await asyncio.sleep(0.01)
    follower.cancel()
    with pytest.raises(asyncio.CancelledError):
        await follower
    assert await leader == ("deck", "computed")
    assert cache.computes == 1


async def test_a_follower_stops_waiting_for_a_stuck_leader() -> None:
    flight, cache = SingleFlight[str](), Cache()
    release = asyncio.Event()

    async def stuck() -> str:
        await release.wait()
        return "late"

    leader = asyncio.create_task(flight.do("k", lookup=cache.lookup, compute=stuck, wait_seconds=5))
    await asyncio.sleep(0.01)

    started = asyncio.get_running_loop().time()
    value, how = await flight.do(
        "k", lookup=cache.lookup, compute=_compute(cache, delay=0.0), wait_seconds=0.1
    )
    elapsed = asyncio.get_running_loop().time() - started
    assert (value, how) == ("deck", "fallback")
    assert 0.09 <= elapsed < 1.0, "bounded by wait_seconds, not by the leader"

    release.set()
    assert await leader == ("late", "computed")


async def test_leader_does_not_unregister_someone_elses_flight() -> None:
    """After a timeout a fallback runs unregistered; it must not disturb the registry."""
    flight, cache = SingleFlight[str](), Cache()
    release = asyncio.Event()

    async def stuck() -> str:
        await release.wait()
        return "late"

    leader = asyncio.create_task(flight.do("k", lookup=cache.lookup, compute=stuck, wait_seconds=5))
    await asyncio.sleep(0.01)
    await flight.do("k", lookup=cache.lookup, compute=_compute(cache, delay=0), wait_seconds=0.05)
    assert flight.in_flight == 1, "the real leader is still registered"
    release.set()
    await leader
    assert flight.in_flight == 0


def test_works_across_event_loops() -> None:
    flight, cache = SingleFlight[str](), Cache()

    async def burst() -> None:
        cache.value = None
        results = await asyncio.gather(
            *(flight.do("k", lookup=cache.lookup, compute=_compute(cache), wait_seconds=5) for _ in range(5))
        )
        assert {v for v, _ in results} == {"deck"}

    asyncio.run(burst())
    asyncio.run(burst())
