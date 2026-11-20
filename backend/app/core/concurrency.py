"""Process-wide cap on how much expensive work runs at once.

A semaphore with a *bounded* wait. Over the cap a request waits briefly for a slot
(a burst of a few real visitors is absorbed) and is then refused with 503 +
Retry-After, instead of piling up behind work it can never overtake. The queue itself
is capped as well, so a flood costs a counter increment, not a parked coroutine each.

Scope is one process. With several workers the effective cap is ``limit * workers``;
production runs one (render.yaml), and a Redis-backed counter would be a new
requirement for no gain at that size.
"""

from __future__ import annotations

import asyncio
import time
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.timing import add_phase_seconds
from app.domain.exceptions import AppError


class ServiceBusyError(AppError):
    """Every slot is taken and the wait ran out (or the queue was already full)."""

    def __init__(self, message: str, *, retry_after: int, code: str = "busy") -> None:
        super().__init__(
            message,
            status_code=503,
            code=code,
            headers={"Retry-After": str(max(int(retry_after), 1))},
        )


class ConcurrencyGate:
    def __init__(
        self,
        limit: int,
        *,
        max_wait_seconds: float,
        max_waiters: int | None = None,
        retry_after_seconds: int = 2,
        busy_message: str = "This is busy right now. Please try again in a moment.",
        busy_code: str = "busy",
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self.limit = limit
        self.max_wait_seconds = max_wait_seconds
        # Enough to absorb a burst, not enough to hide a flood.
        self.max_waiters = limit * 4 if max_waiters is None else max_waiters
        self._retry_after = retry_after_seconds
        self._message = busy_message
        self._code = busy_code
        self._waiting = 0
        # A semaphore binds to the event loop it first has to wait on. Production has
        # one loop, but tests create several, so keep one semaphore per loop.
        self._semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
            weakref.WeakKeyDictionary()
        )

    def _semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        sem = self._semaphores.get(loop)
        if sem is None:
            sem = self._semaphores[loop] = asyncio.Semaphore(self.limit)
        return sem

    @property
    def waiting(self) -> int:
        return self._waiting

    def _busy(self) -> ServiceBusyError:
        return ServiceBusyError(self._message, retry_after=self._retry_after, code=self._code)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        waited_from = time.perf_counter()
        try:
            await self._acquire()
        finally:
            # Time spent queueing for a slot, refused or not, for the timing log.
            add_phase_seconds("gate_wait", time.perf_counter() - waited_from)
        try:
            yield
        finally:
            self._semaphore().release()

    async def _acquire(self) -> None:
        sem = self._semaphore()
        if sem.locked():
            if self._waiting >= self.max_waiters or self.max_wait_seconds <= 0:
                raise self._busy()
            self._waiting += 1
            try:
                await asyncio.wait_for(sem.acquire(), timeout=self.max_wait_seconds)
            except TimeoutError:
                raise self._busy() from None
            finally:
                self._waiting -= 1
        else:
            await sem.acquire()


_guest_gate: ConcurrencyGate | None = None
_guest_gate_config: tuple[int, float] | None = None


def get_guest_rank_gate(limit: int, max_wait_seconds: float) -> ConcurrencyGate:
    """The one gate guest ranking shares. Rebuilt only if the configuration changed."""
    global _guest_gate, _guest_gate_config
    config = (limit, max_wait_seconds)
    if _guest_gate is None or _guest_gate_config != config:
        _guest_gate = ConcurrencyGate(
            limit,
            max_wait_seconds=max_wait_seconds,
            busy_message="Recommendations are busy right now. Please try again in a moment.",
            busy_code="guest_busy",
        )
        _guest_gate_config = config
    return _guest_gate


def reset_guest_rank_gate() -> None:
    """Forget the shared gate (test isolation only)."""
    global _guest_gate, _guest_gate_config
    _guest_gate = None
    _guest_gate_config = None
