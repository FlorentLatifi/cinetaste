"""The cap on concurrent guest ranking: it holds, it refuses politely, it never leaks."""

from __future__ import annotations

import asyncio

import pytest

from app.core.concurrency import (
    ConcurrencyGate,
    ServiceBusyError,
    get_guest_rank_gate,
    reset_guest_rank_gate,
)


async def _hold(gate: ConcurrencyGate, release: asyncio.Event, entered: asyncio.Event | None = None):
    async with gate.slot():
        if entered is not None:
            entered.set()
        await release.wait()


async def test_never_more_than_limit_run_at_once() -> None:
    gate = ConcurrencyGate(3, max_wait_seconds=5, max_waiters=100)
    running = peak = 0

    async def work() -> None:
        nonlocal running, peak
        async with gate.slot():
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1

    await asyncio.gather(*(work() for _ in range(25)))
    assert peak == 3


async def test_a_burst_over_the_limit_waits_instead_of_failing() -> None:
    gate = ConcurrencyGate(1, max_wait_seconds=2)
    release, entered = asyncio.Event(), asyncio.Event()
    first = asyncio.create_task(_hold(gate, release, entered))
    await entered.wait()

    second_in = asyncio.Event()
    second = asyncio.create_task(_hold(gate, asyncio.Event(), second_in))
    await asyncio.sleep(0.05)
    assert not second_in.is_set(), "must wait while the slot is taken"
    assert gate.waiting == 1

    release.set()
    await first
    await asyncio.wait_for(second_in.wait(), timeout=1)  # admitted once the slot freed
    second.cancel()


async def test_saturated_gate_refuses_with_503_and_retry_after() -> None:
    gate = ConcurrencyGate(
        1, max_wait_seconds=0.05, retry_after_seconds=3, busy_code="guest_busy"
    )
    release, entered = asyncio.Event(), asyncio.Event()
    holder = asyncio.create_task(_hold(gate, release, entered))
    await entered.wait()

    with pytest.raises(ServiceBusyError) as excinfo:
        async with gate.slot():
            pytest.fail("must not be admitted")
    err = excinfo.value
    assert err.status_code == 503
    assert err.code == "guest_busy"
    assert err.headers["Retry-After"] == "3"
    assert gate.waiting == 0, "a refused waiter must not stay counted"

    release.set()
    await holder


async def test_zero_wait_refuses_immediately() -> None:
    gate = ConcurrencyGate(1, max_wait_seconds=0)
    release, entered = asyncio.Event(), asyncio.Event()
    holder = asyncio.create_task(_hold(gate, release, entered))
    await entered.wait()
    with pytest.raises(ServiceBusyError):
        async with gate.slot():
            pass
    release.set()
    await holder


async def test_full_queue_refuses_without_waiting() -> None:
    gate = ConcurrencyGate(1, max_wait_seconds=30, max_waiters=2)
    release, entered = asyncio.Event(), asyncio.Event()
    holder = asyncio.create_task(_hold(gate, release, entered))
    await entered.wait()
    waiters = [asyncio.create_task(_hold(gate, asyncio.Event())) for _ in range(2)]
    await asyncio.sleep(0.05)
    assert gate.waiting == 2

    started = asyncio.get_running_loop().time()
    with pytest.raises(ServiceBusyError):
        async with gate.slot():
            pass
    assert asyncio.get_running_loop().time() - started < 0.5, "queue-full must not wait 30s"

    for task in waiters:
        task.cancel()
    release.set()
    await holder
    await asyncio.gather(*waiters, return_exceptions=True)
    assert gate.waiting == 0


async def test_slot_is_released_when_the_work_raises() -> None:
    gate = ConcurrencyGate(1, max_wait_seconds=0)
    with pytest.raises(RuntimeError):
        async with gate.slot():
            raise RuntimeError("ranking failed")
    async with gate.slot():  # would raise ServiceBusyError if the slot had leaked
        pass


async def test_timed_out_waiters_do_not_leak_capacity() -> None:
    gate = ConcurrencyGate(2, max_wait_seconds=0.02)
    release = asyncio.Event()
    holders = [asyncio.create_task(_hold(gate, release)) for _ in range(2)]
    await asyncio.sleep(0.02)

    for _ in range(10):
        with pytest.raises(ServiceBusyError):
            async with gate.slot():
                pass
    release.set()
    await asyncio.gather(*holders)

    # All capacity is back: two slots can be held together again.
    release2 = asyncio.Event()
    again = [asyncio.create_task(_hold(gate, release2, e)) for e in (asyncio.Event(), asyncio.Event())]
    await asyncio.sleep(0.02)
    assert gate.waiting == 0
    release2.set()
    await asyncio.gather(*again)


def test_gate_works_across_event_loops() -> None:
    """Tests (and reloads) create several loops; a semaphore must not stick to the first."""
    gate = ConcurrencyGate(1, max_wait_seconds=1)

    async def contend() -> None:
        release, entered = asyncio.Event(), asyncio.Event()
        holder = asyncio.create_task(_hold(gate, release, entered))
        await entered.wait()
        waiter = asyncio.create_task(_hold(gate, asyncio.Event(), asyncio.Event()))
        await asyncio.sleep(0.01)  # the waiter now needs the semaphore's loop-bound future
        release.set()
        await holder
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)

    asyncio.run(contend())
    asyncio.run(contend())


def test_invalid_limit_is_rejected() -> None:
    with pytest.raises(ValueError):
        ConcurrencyGate(0, max_wait_seconds=1)


def test_shared_guest_gate_is_one_instance_until_config_changes() -> None:
    reset_guest_rank_gate()
    first = get_guest_rank_gate(4, 2.0)
    assert get_guest_rank_gate(4, 2.0) is first
    changed = get_guest_rank_gate(2, 2.0)
    assert changed is not first and changed.limit == 2
    reset_guest_rank_gate()
