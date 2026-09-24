"""Key-value store for response caching and rate limiting.

* ``REDIS_URL`` set   → Redis, with short timeouts. If Redis errors, requests use
  an in-process store for ``_REDIS_RETRY_SECONDS`` and one warning is logged, so
  an outage degrades features instead of spamming stack traces or hanging.
* ``REDIS_URL`` empty → in-process store. On a single-process deploy (Render
  free tier) that is equivalent; with several workers each keeps its own copy,
  which only makes rate limits per worker and caches less effective.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Protocol

from redis.asyncio import Redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_REDIS_RETRY_SECONDS = 30.0
_MEMORY_MAX_ENTRIES = 20_000
# Rate-limit counters get their own budget so cached slates can never evict
# them: dropping a counter silently resets someone's attempt budget to zero.
_RATE_LIMIT_MAX_ENTRIES = 50_000


class KeyValueStore(Protocol):
    backend: str

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None: ...

    async def delete_prefix(self, prefix: str) -> None: ...

    async def track(self, index_key: str, member: str, *, ttl_seconds: int) -> None:
        """Remember that ``member`` is a key this index is responsible for."""
        ...

    async def drop_tracked(self, index_key: str) -> None:
        """Delete every key the index knows about, and the index itself."""
        ...

    async def hit(self, key: str, *, window_seconds: int) -> tuple[int, int]:
        """Count one hit in a fixed window. Returns (hits so far, seconds left)."""
        ...

    async def ping(self) -> bool: ...

    async def close(self) -> None: ...


class MemoryStore:
    """Bounded in-process store with per-key expiry (LRU eviction when full)."""

    backend = "memory"

    def __init__(self, max_entries: int = _MEMORY_MAX_ENTRIES) -> None:
        self._data: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._sets: dict[str, set[str]] = {}
        self._max_entries = max_entries

    def _live(self, key: str) -> tuple[str, float] | None:
        item = self._data.get(key)
        if item is None:
            return None
        if item[1] <= time.monotonic():
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return item

    def _put(self, key: str, value: str, expires_at: float) -> None:
        self._data[key] = (value, expires_at)
        self._data.move_to_end(key)
        while len(self._data) > self._max_entries:
            self._data.popitem(last=False)

    async def get(self, key: str) -> str | None:
        item = self._live(key)
        return item[0] if item else None

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        self._put(key, value, time.monotonic() + max(ttl_seconds, 1))

    async def delete_prefix(self, prefix: str) -> None:
        for key in [k for k in self._data if k.startswith(prefix)]:
            del self._data[key]

    async def track(self, index_key: str, member: str, *, ttl_seconds: int) -> None:
        self._sets.setdefault(index_key, set()).add(member)

    async def drop_tracked(self, index_key: str) -> None:
        for key in self._sets.pop(index_key, ()):
            self._data.pop(key, None)

    async def hit(self, key: str, *, window_seconds: int) -> tuple[int, int]:
        now = time.monotonic()
        item = self._live(key)
        if item is None:
            count, expires_at = 1, now + window_seconds
        else:
            count, expires_at = int(item[0]) + 1, item[1]
        self._put(key, str(count), expires_at)
        return count, max(int(expires_at - now), 1)

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self._data.clear()
        self._sets.clear()


class RedisStore:
    backend = "redis"

    def __init__(self, url: str) -> None:
        self._client = Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )

    async def get(self, key: str) -> str | None:
        return await self._client.get(key)

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        await self._client.set(key, value, ex=max(ttl_seconds, 1))

    async def delete_prefix(self, prefix: str) -> None:
        batch: list[str] = []
        async for key in self._client.scan_iter(match=f"{prefix}*", count=200):
            batch.append(key)
            if len(batch) >= 200:
                await self._client.delete(*batch)
                batch.clear()
        if batch:
            await self._client.delete(*batch)

    async def track(self, index_key: str, member: str, *, ttl_seconds: int) -> None:
        # The index outlives the entries it points at, so a slate that expires
        # on its own leaves a dead member behind. UNLINK on a missing key is a
        # no-op, so that costs nothing but a little memory until the index
        # itself expires.
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.sadd(index_key, member)
            pipe.expire(index_key, max(ttl_seconds, 1) * 2)
            await pipe.execute()

    async def drop_tracked(self, index_key: str) -> None:
        members = await self._client.smembers(index_key)
        if members:
            # UNLINK, not DEL: frees memory on a background thread.
            await self._client.unlink(*members, index_key)
        else:
            await self._client.unlink(index_key)

    async def hit(self, key: str, *, window_seconds: int) -> tuple[int, int]:
        # SET NX EX + INCR in one transaction: the window's expiry is set exactly
        # once, so a crash between commands can't leave a counter without a TTL.
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.set(key, 0, ex=window_seconds, nx=True)
            pipe.incr(key)
            pipe.ttl(key)
            _created, count, ttl = await pipe.execute()
        return int(count), max(int(ttl), 1)

    async def ping(self) -> bool:
        return bool(await self._client.ping())

    async def flush(self) -> None:
        await self._client.flushdb()

    async def close(self) -> None:
        await self._client.aclose()


class FallbackStore:
    """Redis first; while Redis is failing, serve from memory and retry later."""

    def __init__(self, primary: RedisStore, fallback: MemoryStore) -> None:
        self._primary = primary
        self._fallback = fallback
        self._retry_at = 0.0

    @property
    def backend(self) -> str:
        return "memory (redis unavailable)" if self._degraded() else "redis"

    def _degraded(self) -> bool:
        return time.monotonic() < self._retry_at

    def _trip(self, exc: Exception) -> None:
        if not self._degraded():
            logger.warning(
                "cache_redis_unavailable error=%s; using in-process store for %ss",
                type(exc).__name__,
                int(_REDIS_RETRY_SECONDS),
            )
        self._retry_at = time.monotonic() + _REDIS_RETRY_SECONDS

    async def _call(self, name: str, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        if not self._degraded():
            try:
                return await getattr(self._primary, name)(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — any Redis failure degrades
                self._trip(exc)
        return await getattr(self._fallback, name)(*args, **kwargs)

    async def get(self, key: str) -> str | None:
        return await self._call("get", key)

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        await self._call("set", key, value, ttl_seconds=ttl_seconds)

    async def delete_prefix(self, prefix: str) -> None:
        # Invalidate both copies so a Redis recovery can't resurrect stale data.
        await self._fallback.delete_prefix(prefix)
        await self._call("delete_prefix", prefix)

    async def track(self, index_key: str, member: str, *, ttl_seconds: int) -> None:
        await self._call("track", index_key, member, ttl_seconds=ttl_seconds)

    async def drop_tracked(self, index_key: str) -> None:
        # Both copies: a Redis recovery must not resurrect an entry we dropped
        # while degraded.
        await self._fallback.drop_tracked(index_key)
        await self._call("drop_tracked", index_key)

    async def hit(self, key: str, *, window_seconds: int) -> tuple[int, int]:
        return await self._call("hit", key, window_seconds=window_seconds)

    async def ping(self) -> bool:
        try:
            ok = await self._primary.ping()
        except Exception as exc:  # noqa: BLE001
            self._trip(exc)
            return False
        if ok:
            self._retry_at = 0.0
        return ok

    async def close(self) -> None:
        await self._primary.close()
        await self._fallback.close()


_store: KeyValueStore | None = None
_rate_limit_store: KeyValueStore | None = None


def get_store() -> KeyValueStore:
    """Shared store for response caching (For You slates, watch providers)."""
    global _store
    if _store is None:
        url = get_settings().redis_url.strip()
        _store = FallbackStore(RedisStore(url), MemoryStore()) if url else MemoryStore()
    return _store


def get_rate_limit_store() -> KeyValueStore:
    """Store for rate-limit counters.

    With Redis this is the same connection — keys are namespaced and Redis has
    its own eviction policy. Without Redis it is a *separate* in-process map:
    the shared one evicts by LRU at 20k entries, so a burst of cached slates
    could quietly drop the counter that was holding a brute-force attempt back.
    """
    global _rate_limit_store
    if _rate_limit_store is None:
        url = get_settings().redis_url.strip()
        _rate_limit_store = (
            get_store() if url else MemoryStore(max_entries=_RATE_LIMIT_MAX_ENTRIES)
        )
    return _rate_limit_store


async def close_store() -> None:
    global _store, _rate_limit_store
    # The rate-limit store is either the shared one (Redis) or its own memory
    # map; close it first and only close the shared one once.
    if _rate_limit_store is not None and _rate_limit_store is not _store:
        await _rate_limit_store.close()
    _rate_limit_store = None
    if _store is not None:
        await _store.close()
        _store = None


async def _empty(store: KeyValueStore) -> None:
    if isinstance(store, FallbackStore):
        try:
            await store._primary.flush()
        except Exception:  # noqa: BLE001
            pass
        await store._fallback.close()
    else:
        await store.close()


async def reset_cache_for_tests() -> None:
    """Empty every cache/rate-limit bucket (test isolation only)."""
    await _empty(get_store())
    rate_store = get_rate_limit_store()
    if rate_store is not get_store():
        await _empty(rate_store)
