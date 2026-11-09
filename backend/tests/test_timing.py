"""Request timing: stages, the header format, and staying out of the way."""

from __future__ import annotations

import asyncio
import re

from app.core import timing


def test_stage_is_a_no_op_when_nothing_is_timed() -> None:
    async def run() -> None:
        with timing.stage("rank"):
            pass
        assert timing.current() is None

    asyncio.run(run())


def test_stages_accumulate_and_render_as_server_timing() -> None:
    async def run() -> str:
        clock = timing.start()
        with timing.stage("candidates"):
            await asyncio.sleep(0.01)
        with timing.stage("candidates"):
            pass
        clock.db_ms, clock.queries = 4.0, 3
        return clock.server_timing()

    header = asyncio.run(run())
    entries = dict(part.split(";", 1) for part in header.split(", "))
    assert set(entries) == {"candidates", "db", "app"}
    assert float(re.search(r"dur=([\d.]+)", entries["candidates"]).group(1)) >= 10
    assert entries["db"] == 'dur=4.0;desc="3 queries"'


def test_timing_does_not_leak_between_tasks() -> None:
    async def other() -> object:
        return timing.current()

    async def run() -> tuple[object, object]:
        clock = timing.start()
        seen_in_sibling = await asyncio.create_task(other())  # copies context: same clock
        return clock, seen_in_sibling

    clock, seen = asyncio.run(run())
    assert seen is clock
    assert asyncio.run(other()) is None  # a fresh run starts untimed
