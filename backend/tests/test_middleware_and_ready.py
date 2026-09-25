"""Rate-limit client IP resolution and the cache/rate-limit store."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.middleware import (
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    client_ip,
)
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


def test_client_ip_ignores_a_chain_shorter_than_the_proxy_count() -> None:
    """The origin host is reachable without going through the CDN.

    Such a request carries fewer appended entries than we are configured for,
    so every value in the header was written by the caller. Falling back to the
    left-most entry (as this used to) let the caller choose its own rate-limit
    bucket and cycle it on every attempt.
    """
    # Configured for two proxies, only one appended: the caller wrote the rest.
    assert client_ip(_request("1.1.1.1"), trusted_proxy_hops=2) == "9.9.9.9"
    assert client_ip(_request("1.1.1.1, 2.2.2.2, 3.3.3.3"), trusted_proxy_hops=4) == "9.9.9.9"


def test_client_ip_rejects_entries_that_are_not_addresses() -> None:
    """A forged entry can be any string; it must not become a bucket key."""
    assert client_ip(_request("not-an-ip, 10.0.0.1"), trusted_proxy_hops=2) == "9.9.9.9"
    assert client_ip(_request("<script>, 10.0.0.1"), trusted_proxy_hops=2) == "9.9.9.9"


def test_client_ip_normalises_ports_and_ipv6() -> None:
    """Some proxies append host:port or a bracketed IPv6 address."""
    assert client_ip(_request("203.0.113.7:51234, 10.0.0.1"), trusted_proxy_hops=2) == "203.0.113.7"
    assert client_ip(_request("[2001:db8::1]:443, 10.0.0.1"), trusted_proxy_hops=2) == "2001:db8::1"


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


# --------------------------------------------------------------- limit buckets


def _limiter(**overrides) -> RateLimitMiddleware:
    data = {
        "jwt_secret": "unit-test-secret-key-at-least-32-chars!",
        "database_url": "postgresql+asyncpg://u:p@localhost/x",
    }
    data.update(overrides)
    return RateLimitMiddleware(app=None, settings=Settings(**data))


def test_limit_families_do_not_share_a_counter() -> None:
    """A family must not share a bucket with one that has a different ceiling.

    ``/auth/refresh`` is allowed twice as many calls as ``/auth/login``. While
    both wrote to one counter, ordinary refresh traffic spent the login budget
    and a login flood locked out refreshes.
    """
    limiter = _limiter(rate_limit_auth_requests=20)

    login_max, _, login_family = limiter._limits_for("/api/v1/auth/login")
    refresh_max, _, refresh_family = limiter._limits_for("/api/v1/auth/refresh")
    reset_max, _, reset_family = limiter._limits_for("/api/v1/auth/forgot-password")
    api_max, _, api_family = limiter._limits_for("/api/v1/titles/search")

    assert len({login_family, refresh_family, reset_family, api_family}) == 4
    assert login_max == reset_max == 20
    assert refresh_max == 40
    assert api_max == 120


def test_guest_routes_have_their_own_lower_budget() -> None:
    """Public and one of them ranks a slate: cheaper to abuse than /titles."""
    limiter = _limiter(rate_limit_guest_requests=30)
    for path in ("/api/v1/guest/cards", "/api/v1/guest/recommendations"):
        guest_max, _, family = limiter._limits_for(path)
        assert (guest_max, family) == (30, "guest")
    assert limiter._limits_for("/api/v1/titles/search")[2] == "api"


def test_register_shares_the_login_budget() -> None:
    """Both mint sessions from an email + password, so one budget covers them."""
    limiter = _limiter()
    assert limiter._limits_for("/api/v1/auth/login") == limiter._limits_for("/api/v1/auth/register")


# ----------------------------------------------------------------------- HSTS


def _security_headers(settings: Settings, scheme: str) -> dict[str, str]:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)

    @app.get("/x")
    async def _x() -> dict[str, bool]:
        return {"ok": True}

    base = f"{scheme}://api.example.test"
    with TestClient(app, base_url=base) as client:
        return dict(client.get("/x").headers)


def test_hsts_is_sent_in_production_even_when_the_scheme_looks_plain() -> None:
    """TLS terminates at the platform proxy.

    ``request.url.scheme`` is only rewritten from X-Forwarded-Proto when uvicorn
    trusts the peer, which it does not by default, so keying HSTS off the scheme
    meant production never sent the header at all.
    """
    prod = Settings(
        app_env="production",
        app_debug=False,
        jwt_secret="a-strong-unique-production-secret-value-1234",
        database_url="postgresql+asyncpg://user:pw@db.example.com/ct",
        cors_origins="https://cinetaste.vercel.app",
    )
    headers = _security_headers(prod, "http")
    assert "max-age=31536000" in headers["strict-transport-security"]


def test_hsts_is_not_sent_over_plain_http_locally() -> None:
    local = Settings(
        jwt_secret="unit-test-secret-key-at-least-32-chars!",
        database_url="postgresql+asyncpg://u:p@localhost/x",
    )
    assert "strict-transport-security" not in _security_headers(local, "http")
    assert "strict-transport-security" in _security_headers(local, "https")
