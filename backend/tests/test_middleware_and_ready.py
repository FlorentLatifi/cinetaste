"""Rate-limit client IP resolution and the cache/rate-limit store."""

from unittest.mock import MagicMock

import pytest

from app.core.middleware import client_ip
from app.infrastructure.cache import FallbackStore, MemoryStore


def _request(xff: str | None, host: str | None = "9.9.9.9") -> MagicMock:
    request = MagicMock()
    request.headers = {"x-forwarded-for": xff} if xff is not None else {}
    if host is None:
        request.client = None
    else:
        request.client.host = host
    return request


def test_client_ip_ignores_xff_without_trusted_proxies() -> None:
    assert client_ip(_request("1.2.3.4, 10.0.0.1"), trusted_proxy_hops=0) == "9.9.9.9"


def test_client_ip_uses_entry_written_by_trusted_proxy() -> None:
    # One proxy (e.g. Render) appended 10.0.0.1 = the address it saw.
    assert client_ip(_request("1.2.3.4, 10.0.0.1"), trusted_proxy_hops=1) == "10.0.0.1"
    # Two proxies (Vercel rewrite → Render): the client is two from the right.
    assert client_ip(_request("1.2.3.4, 10.0.0.1"), trusted_proxy_hops=2) == "1.2.3.4"


def test_client_ip_cannot_be_spoofed_by_prepending() -> None:
    """A client-supplied X-Forwarded-For only adds entries on the left."""
    spoofed = _request("6.6.6.6, 7.7.7.7, 203.0.113.9")
    assert client_ip(spoofed, trusted_proxy_hops=1) == "203.0.113.9"


def test_client_ip_falls_back_without_client() -> None:
    assert client_ip(_request(None, host=None), trusted_proxy_hops=0) == "unknown"


@pytest.mark.asyncio
async def test_memory_store_counts_hits_per_window() -> None:
    store = MemoryStore()
    assert (await store.hit("k", window_seconds=60))[0] == 1
    count, retry_after = await store.hit("k", window_seconds=60)
    assert count == 2
    assert 1 <= retry_after <= 60


@pytest.mark.asyncio
async def test_memory_store_get_set_and_prefix_delete() -> None:
    store = MemoryStore()
    await store.set("slate:u1:1", "a", ttl_seconds=60)
    await store.set("slate:u1:2", "b", ttl_seconds=60)
    await store.set("slate:u2:1", "c", ttl_seconds=60)
    await store.delete_prefix("slate:u1:")
    assert await store.get("slate:u1:1") is None
    assert await store.get("slate:u2:1") == "c"


@pytest.mark.asyncio
async def test_memory_store_evicts_oldest_when_full() -> None:
    store = MemoryStore(max_entries=2)
    for key in ("a", "b", "c"):
        await store.set(key, key, ttl_seconds=60)
    assert await store.get("a") is None
    assert await store.get("c") == "c"


class _BrokenRedis:
    backend = "redis"

    def __init__(self) -> None:
        self.calls = 0

    async def hit(self, key: str, *, window_seconds: int) -> tuple[int, int]:
        self.calls += 1
        raise ConnectionError("redis down")

    async def ping(self) -> bool:
        raise ConnectionError("redis down")


@pytest.mark.asyncio
async def test_fallback_store_keeps_rate_limiting_when_redis_is_down() -> None:
    primary = _BrokenRedis()
    store = FallbackStore(primary, MemoryStore())  # type: ignore[arg-type]
    assert (await store.hit("rl:x", window_seconds=60))[0] == 1
    assert (await store.hit("rl:x", window_seconds=60))[0] == 2
    # After the first failure Redis is not retried on every request.
    assert primary.calls == 1
    assert store.backend == "memory (redis unavailable)"
