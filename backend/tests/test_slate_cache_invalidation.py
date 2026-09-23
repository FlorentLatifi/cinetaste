"""Cached slates: how they are invalidated, and how big they are.

`invalidate_user` runs on every rating, watchlist add and undo. It used to
delete by prefix, which on Redis means SCAN — a walk of the entire keyspace.
Measured at 10,000 titles: 1.1 ms with 100 keys in the store, 449 ms with
100,000. The cost grew with how many *other* people were using the product.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.infrastructure.cache import MemoryStore


@pytest.mark.asyncio
async def test_tracked_keys_are_deleted_and_others_are_left_alone() -> None:
    store = MemoryStore()
    mine, theirs = uuid4(), uuid4()

    for size in (10, 20):
        key = f"slate:{mine}:1:{size}"
        await store.set(key, "payload", ttl_seconds=600)
        await store.track(f"slateidx:{mine}", key, ttl_seconds=600)

    other_key = f"slate:{theirs}:1:20"
    await store.set(other_key, "payload", ttl_seconds=600)
    await store.track(f"slateidx:{theirs}", other_key, ttl_seconds=600)

    await store.drop_tracked(f"slateidx:{mine}")

    assert await store.get(f"slate:{mine}:1:10") is None
    assert await store.get(f"slate:{mine}:1:20") is None
    assert await store.get(other_key) == "payload"


@pytest.mark.asyncio
async def test_dropping_an_index_twice_is_harmless() -> None:
    """Two ratings in quick succession both invalidate."""
    store = MemoryStore()
    user = uuid4()
    await store.set(f"slate:{user}:1:20", "payload", ttl_seconds=600)
    await store.track(f"slateidx:{user}", f"slate:{user}:1:20", ttl_seconds=600)

    await store.drop_tracked(f"slateidx:{user}")
    await store.drop_tracked(f"slateidx:{user}")  # must not raise


@pytest.mark.asyncio
async def test_dropping_an_unknown_index_is_harmless() -> None:
    await MemoryStore().drop_tracked(f"slateidx:{uuid4()}")


@pytest.mark.asyncio
async def test_an_entry_that_expired_on_its_own_is_not_a_problem() -> None:
    """The index outlives what it points at; a dead member is a no-op."""
    store = MemoryStore()
    user = uuid4()
    key = f"slate:{user}:1:20"
    await store.track(f"slateidx:{user}", key, ttl_seconds=600)  # never set

    await store.drop_tracked(f"slateidx:{user}")
    assert await store.get(key) is None


# ---------------------------------------------------------------- payload size


def _payload(reason_count: int = 3) -> str:
    """The shape for_you writes, as of the evidence removal."""
    return json.dumps(
        {
            "slate_id": str(uuid4()),
            "items": [
                {
                    "title_id": str(uuid4()),
                    "score": 0.8123,
                    "reasons": [
                        {"code": "because_you_liked", "message": "Because you liked X"}
                        for _ in range(reason_count)
                    ],
                }
                for _ in range(20)
            ],
        }
    )


def test_cached_reasons_carry_no_evidence() -> None:
    """`evidence` was 781 of ~824 bytes per item and nothing renders it.

    It still exists on the domain Reason for explanations and tests — this is
    about what crosses the wire and sits in Redis.
    """
    payload = json.loads(_payload())
    for item in payload["items"]:
        for reason in item["reasons"]:
            assert set(reason) == {"code", "message"}


def test_a_slate_stays_well_under_ten_kilobytes() -> None:
    """16.1 KiB per slate is 160 MB at 10k users — more than a free Redis holds."""
    assert len(_payload()) < 10 * 1024
