"""Per-request phase timing, logged as one line.

Why: a guest slate measured 5.9 s from outside while the same code takes ~50-70 ms on a
local database. Nothing in the access log says where the other seconds go. This answers
it from one real request, without load: how long each phase took, how many SQL statements
ran, how much of the time was spent inside the database driver, and how long the slowest
statement took.

Reading the line (``guest_slate_timing``):

* ``db_ms`` is time inside cursor executions as the app sees it, so it **includes the
  network round trip to the database**. If ``db_ms`` is most of ``total_ms`` the database
  link (latency, a cold or suspended instance) is the cost; if ``db_max_ms`` is large with
  few statements, one query is slow; if neither is large, the time is in the process
  (CPU on a small instance, ``gate_wait_ms`` queueing).
* A phase's time minus the ``db_ms`` inside it is Python work plus what the driver does
  not report as a cursor execute: pooled-connection checkout and pre-ping, BEGIN and
  ROLLBACK. A large gap there is also a database-link signal.
* ``flight_wait_ms`` is time a request spent waiting for another request computing the same
  thing (single-flight); ``outcome=shared`` means it used that result and did no work itself.
* ``pool`` says whether the shared popularity slice was served from memory or loaded;
  ``pool_load_ms`` (only when loaded) is part of ``candidates_ms``, not on top of it.
* ``validate`` + ``profile`` + ``candidates`` + ``rank`` + ``serialize`` are the miss path;
  ``cache_ms`` on a hit includes re-reading the titles (2 statements).

Mechanism: a ``ContextVar`` holds the active timer, so services record phases without it
being threaded through every signature, and it is a no-op when nothing is timing (For You,
scripts, tests). SQL statements are counted by engine events that do nothing unless a
timer is active.
"""

from __future__ import annotations

import logging
import time
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

_current: ContextVar[PhaseTimer | None] = ContextVar("phase_timer", default=None)

# Product target for the guest slate is p95 < 800 ms; a request over it is logged at
# WARNING so it stays visible if INFO is ever filtered out of the log drain.
SLOW_REQUEST_MS = 800.0


class PhaseTimer:
    def __init__(self) -> None:
        self._started = time.perf_counter()
        self._phases: dict[str, float] = {}
        self.notes: dict[str, str] = {}
        self.db_statements = 0
        self.db_seconds = 0.0
        self.db_max_seconds = 0.0

    def add(self, name: str, seconds: float) -> None:
        self._phases[name] = self._phases.get(name, 0.0) + seconds

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:  # a phase that raised still says how long it took
            self.add(name, time.perf_counter() - started)

    def record_statement(self, seconds: float) -> None:
        self.db_statements += 1
        self.db_seconds += seconds
        self.db_max_seconds = max(self.db_max_seconds, seconds)

    @property
    def total_ms(self) -> float:
        return (time.perf_counter() - self._started) * 1000

    def fields(self) -> str:
        parts = [f"total_ms={self.total_ms:.1f}"]
        parts += [f"{name}_ms={seconds * 1000:.1f}" for name, seconds in self._phases.items()]
        parts += [
            f"db_statements={self.db_statements}",
            f"db_ms={self.db_seconds * 1000:.1f}",
            f"db_max_ms={self.db_max_seconds * 1000:.1f}",
        ]
        parts += [f"{key}={value}" for key, value in self.notes.items()]
        return " ".join(parts)

    def log(self, logger: logging.Logger, event_name: str, **fields: Any) -> None:
        head = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
        line = f"{event_name} {head} {self.fields()}"
        level = logging.WARNING if self.total_ms > SLOW_REQUEST_MS else logging.INFO
        logger.log(level, line)


def start_timer() -> tuple[PhaseTimer, Token]:
    timer = PhaseTimer()
    return timer, _current.set(timer)


def stop_timer(token: Token) -> None:
    _current.reset(token)


def current_timer() -> PhaseTimer | None:
    return _current.get()


@contextmanager
def phase(name: str) -> Iterator[None]:
    """Time a block against the active timer; does nothing when none is active."""
    timer = _current.get()
    if timer is None:
        yield
        return
    with timer.phase(name):
        yield


def note(key: str, value: str) -> None:
    timer = _current.get()
    if timer is not None:
        timer.notes[key] = value


def add_phase_seconds(name: str, seconds: float) -> None:
    timer = _current.get()
    if timer is not None:
        timer.add(name, seconds)


_instrumented: weakref.WeakSet[Engine] = weakref.WeakSet()


def install_statement_timing(engine: Engine) -> None:
    """Count and time every statement while a timer is active. Idempotent."""
    if engine in _instrumented:
        return
    _instrumented.add(engine)

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ANN202
        if _current.get() is not None:
            context._cinetaste_started = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ANN202
        started = getattr(context, "_cinetaste_started", None)
        timer = _current.get()
        if started is not None and timer is not None:
            timer.record_statement(time.perf_counter() - started)
