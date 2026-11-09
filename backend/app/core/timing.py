"""Where a slow request spends its time: database or Python.

A guest slate costs ~65 ms of CPU locally and took 3.9–5.2 s in production.
Local profiling cannot explain that gap — candidates are the remote database
(round trips, cold starts) or the 0.1-CPU instance — and guessing led to an
optimisation that measured as noise. So the slate endpoints measure it:

* named stages (``candidates``, ``rank``, …) timed with :func:`stage`;
* every database round trip, counted and timed through SQLAlchemy cursor
  events, so ``db`` separates waiting on Postgres from computing in Python.

Results go out as a ``Server-Timing`` header (readable in browser dev tools)
and one structured log line per request. Timings only — no identifiers.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from sqlalchemy import event
from sqlalchemy.engine import Engine

_current: ContextVar[RequestTiming | None] = ContextVar("request_timing", default=None)


@dataclass(slots=True)
class RequestTiming:
    started: float = field(default_factory=time.perf_counter)
    stages: dict[str, float] = field(default_factory=dict)
    db_ms: float = 0.0
    queries: int = 0

    def total_ms(self) -> float:
        return (time.perf_counter() - self.started) * 1000

    def server_timing(self) -> str:
        """``Server-Timing`` header value, e.g. ``db;dur=12.0;desc="5 queries"``."""
        parts = [f"{name};dur={ms:.1f}" for name, ms in self.stages.items()]
        parts.append(f'db;dur={self.db_ms:.1f};desc="{self.queries} queries"')
        parts.append(f"app;dur={self.total_ms():.1f}")
        return ", ".join(parts)

    def log_fields(self) -> str:
        stages = " ".join(f"{name}_ms={ms:.1f}" for name, ms in self.stages.items())
        return (
            f"total_ms={self.total_ms():.1f} db_ms={self.db_ms:.1f} "
            f"queries={self.queries} {stages}"
        ).strip()


def start() -> RequestTiming:
    """Begin timing the current request (or task)."""
    timing = RequestTiming()
    _current.set(timing)
    return timing


def current() -> RequestTiming | None:
    return _current.get()


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Time a block under ``name``; a no-op when nothing is being timed."""
    timing = _current.get()
    if timing is None:
        yield
        return
    began = time.perf_counter()
    try:
        yield
    finally:
        timing.stages[name] = timing.stages.get(name, 0.0) + (time.perf_counter() - began) * 1000


_QUERY_STARTED = "ct_query_started"


def install_query_timing(engine: Engine) -> None:
    """Count and time every round trip on ``engine`` against the current request."""

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, _cursor, _statement, _params, _context, _many) -> None:
        if _current.get() is not None:
            conn.info.setdefault(_QUERY_STARTED, []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, _cursor, _statement, _params, _context, _many) -> None:
        timing = _current.get()
        started = conn.info.get(_QUERY_STARTED)
        if timing is None or not started:
            return
        timing.db_ms += (time.perf_counter() - started.pop()) * 1000
        timing.queries += 1
