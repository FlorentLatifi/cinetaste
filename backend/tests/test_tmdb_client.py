"""TMDb client retry behaviour (httpx MockTransport, no network)."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.domain.exceptions import AppError
from app.infrastructure.tmdb.client import TmdbClient


def _client(handler, monkeypatch: pytest.MonkeyPatch) -> TmdbClient:
    monkeypatch.setattr(TmdbClient, "_backoff", staticmethod(lambda attempt, retry_after: 0.0))
    settings = Settings(
        jwt_secret="unit-test-secret-key-at-least-32-chars!",
        database_url="postgresql+asyncpg://u:p@localhost/db",
        tmdb_api_key="test-key",
    )
    return TmdbClient(settings, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_retries_rate_limit_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"retry-after": "1"})
        return httpx.Response(200, json={"genres": [{"id": 18, "name": "Drama"}]})

    client = _client(handler, monkeypatch)
    assert await client.get_genres("movie") == [{"id": 18, "name": "Drama"}]
    assert calls["n"] == 3
    await client.aclose()


@pytest.mark.asyncio
async def test_gives_up_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(lambda request: httpx.Response(429), monkeypatch)
    with pytest.raises(AppError) as exc:
        await client.get_genres("movie")
    assert exc.value.code == "tmdb_rate_limited"
    await client.aclose()


@pytest.mark.asyncio
async def test_invalid_key_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401)

    client = _client(handler, monkeypatch)
    with pytest.raises(AppError) as exc:
        await client.get_genres("movie")
    assert exc.value.code == "tmdb_unauthorized"
    assert calls["n"] == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_network_errors_are_retried_then_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = _client(handler, monkeypatch)
    with pytest.raises(AppError) as exc:
        await client.get_genres("movie")
    assert exc.value.code == "tmdb_unavailable"
    await client.aclose()


def test_httpx_request_logs_are_silenced() -> None:
    """Request URLs carry the TMDb api_key; they must not reach INFO logs."""
    import logging

    from app.core.logging import configure_logging

    configure_logging(debug=True)
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
