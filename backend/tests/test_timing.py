"""Request phase timing: accurate enough to trust, and inert when nothing is timing."""

from __future__ import annotations

import asyncio
import logging
import re

import pytest
from sqlalchemy import create_engine, text

from app.core import timing
from app.core.timing import (
    PhaseTimer,
    add_phase_seconds,
    current_timer,
    install_statement_timing,
    note,
    phase,
    start_timer,
    stop_timer,
)


def test_everything_is_a_noop_without_a_timer() -> None:
    assert current_timer() is None
    with phase("x"):
        pass
    note("k", "v")
    add_phase_seconds("x", 1.0)  # none of these may raise or create state
    assert current_timer() is None


async def test_phases_accumulate_and_report_in_ms() -> None:
    timer, token = start_timer()
    try:
        with phase("a"):
            await asyncio.sleep(0.02)
        with phase("a"):  # the same phase twice adds up
            await asyncio.sleep(0.02)
        with phase("b"):
            pass
        note("pool", "memory")
    finally:
        stop_timer(token)
    fields = dict(part.split("=") for part in timer.fields().split())
    assert 35 <= float(fields["a_ms"]) < 200
    assert float(fields["b_ms"]) < 20
    assert float(fields["total_ms"]) >= float(fields["a_ms"])
    assert fields["pool"] == "memory"
    assert current_timer() is None, "the timer is detached when stopped"


async def test_a_phase_that_raises_still_records_its_time() -> None:
    timer, token = start_timer()
    try:
        with pytest.raises(RuntimeError), phase("boom"):
            await asyncio.sleep(0.01)
            raise RuntimeError
    finally:
        stop_timer(token)
    assert "boom_ms=" in timer.fields()


async def test_concurrent_requests_do_not_share_a_timer() -> None:
    async def one(name: str) -> PhaseTimer:
        timer, token = start_timer()
        try:
            with phase(name):
                await asyncio.sleep(0.01)
        finally:
            stop_timer(token)
        return timer

    first, second = await asyncio.gather(one("alpha"), one("beta"))
    assert "alpha_ms" in first.fields() and "beta_ms" not in first.fields()
    assert "beta_ms" in second.fields() and "alpha_ms" not in second.fields()


def test_statements_are_counted_only_while_a_timer_is_active() -> None:
    engine = create_engine("sqlite://")
    install_statement_timing(engine)
    install_statement_timing(engine)  # idempotent: must not double count
    with engine.connect() as conn:
        conn.execute(text("select 1"))  # no timer: not counted anywhere
        timer, token = start_timer()
        try:
            conn.execute(text("select 1"))
            conn.execute(text("select 2"))
        finally:
            stop_timer(token)
        conn.execute(text("select 3"))  # after stop: not counted
    assert timer.db_statements == 2
    assert 0 <= timer.db_max_seconds <= timer.db_seconds


def test_log_line_is_key_value_info_normally_and_warning_when_slow(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    logger = logging.getLogger("cinetaste.test.timing")
    timer = PhaseTimer()
    timer.record_statement(0.004)
    with caplog.at_level(logging.INFO, logger=logger.name):
        timer.log(logger, "guest_slate_timing", outcome="miss", request_id="r1", skipped=None)
    record = caplog.records[-1]
    assert record.levelno == logging.INFO
    line = record.getMessage()
    assert line.startswith("guest_slate_timing outcome=miss request_id=r1 total_ms=")
    assert "skipped" not in line, "None fields are left out"
    assert re.search(r"db_statements=1 db_ms=4\.\d db_max_ms=4\.\d", line)

    monkeypatch.setattr(timing, "SLOW_REQUEST_MS", 0.0)
    with caplog.at_level(logging.INFO, logger=logger.name):
        timer.log(logger, "guest_slate_timing", outcome="miss")
    assert caplog.records[-1].levelno == logging.WARNING, "over the target stays visible"
