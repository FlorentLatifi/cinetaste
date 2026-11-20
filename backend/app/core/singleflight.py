"""Single-flight: when many callers miss the same cache key at once, compute it once.

Without it a cold or just-expired entry is a stampede: N simultaneous visitors each run
the same query, hold N pooled database connections for it, and all wait for the slowest.
With it the first caller (the *leader*) does the work and the others (*followers*) wait
for the leader's result.

Failure is deliberately not shared. A follower never inherits the leader's exception or
cancellation, and never queues behind a leader that is stuck:

* leader raised, was cancelled (its client went away) or produced nothing -> every
  follower does the work itself, exactly as it would have without single-flight;
* leader still running after ``wait_seconds`` -> that follower stops waiting and does the
  work itself.

So the worst case is the old behaviour, never worse, and a follower needs neither the
cache nor a database connection while it waits.

Scope is one process, keyed by string. With several workers each coalesces its own
callers; that still divides the stampede by the number of workers' requests, and needs
no shared lock. The registry only holds keys with work in progress.

Known benign race: a caller whose cache lookup missed an instant before the leader
finished and unregistered will find no flight and compute once more. One extra
computation, never a wrong answer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

Outcome = Literal["hit", "computed", "shared", "fallback"]


class _Flight[T]:
    __slots__ = ("done", "result")

    def __init__(self) -> None:
        self.done = asyncio.Event()
        self.result: T | None = None


class SingleFlight[T]:
    def __init__(self) -> None:
        self._flights: dict[str, _Flight[T]] = {}

    @property
    def in_flight(self) -> int:
        return len(self._flights)

    async def do(
        self,
        key: str,
        *,
        lookup: Callable[[], Awaitable[T | None]],
        compute: Callable[[], Awaitable[T]],
        wait_seconds: float,
    ) -> tuple[T, Outcome]:
        """The cached value, the leader's result, or a freshly computed one.

        ``lookup`` returns the cached value or None. ``compute`` does the work and is
        responsible for storing it (so later callers hit the cache); its return value is
        also handed to followers directly, so they do not depend on the cache working.
        The result is shared between callers: treat it as read-only.
        """
        cached = await lookup()
        if cached is not None:
            return cached, "hit"

        flight = self._flights.get(key)
        if flight is not None:
            try:
                await asyncio.wait_for(flight.done.wait(), timeout=wait_seconds)
            except TimeoutError:
                pass
            if flight.result is not None:
                return flight.result, "shared"
            return await compute(), "fallback"

        flight = _Flight[T]()
        self._flights[key] = flight
        try:
            flight.result = await compute()
            return flight.result, "computed"
        finally:
            if self._flights.get(key) is flight:
                del self._flights[key]
            flight.done.set()
