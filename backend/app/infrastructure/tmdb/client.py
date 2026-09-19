from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from app.core.config import Settings
from app.domain.exceptions import AppError

logger = logging.getLogger(__name__)

# Transient statuses worth retrying (rate limit + upstream hiccups).
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class TmdbClient:
    """Thin async TMDb v3 client with retries.

    TMDb v3 authenticates with an ``api_key`` query parameter, so request URLs
    contain the key; ``configure_logging`` keeps httpx's per-request INFO logs
    off for that reason.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        max_attempts: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not settings.tmdb_api_key:
            raise AppError(
                "TMDB_API_KEY is not configured. Get a free key at "
                "https://www.themoviedb.org/settings/api",
                status_code=503,
                code="tmdb_not_configured",
            )
        self._base = settings.tmdb_base_url.rstrip("/")
        self._key = settings.tmdb_api_key
        self._max_attempts = max(1, max_attempts)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0), transport=transport
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), 30.0)
            except ValueError:
                pass
        return min(0.5 * 2**attempt, 8.0) + random.uniform(0, 0.25)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = {"api_key": self._key, **(params or {})}
        url = f"{self._base}{path}"
        last_attempt = self._max_attempts - 1
        for attempt in range(self._max_attempts):
            try:
                response = await self._client.get(url, params=query)
            except httpx.TransportError as exc:  # timeouts, connection resets, DNS
                if attempt == last_attempt:
                    raise AppError(
                        "TMDb is unreachable", status_code=503, code="tmdb_unavailable"
                    ) from exc
                await asyncio.sleep(self._backoff(attempt, None))
                continue

            if response.status_code == 401:
                raise AppError("Invalid TMDB API key", status_code=503, code="tmdb_unauthorized")
            if response.status_code in _RETRY_STATUSES and attempt < last_attempt:
                delay = self._backoff(attempt, response.headers.get("retry-after"))
                logger.info(
                    "tmdb_retry path=%s status=%s delay=%.1fs", path, response.status_code, delay
                )
                await asyncio.sleep(delay)
                continue
            if response.status_code == 429:
                raise AppError("TMDB rate limit exceeded", status_code=503, code="tmdb_rate_limited")
            response.raise_for_status()
            return response.json()
        raise AssertionError("unreachable")  # pragma: no cover

    async def get_genres(self, media_type: str) -> list[dict[str, Any]]:
        data = await self._get(f"/genre/{media_type}/list")
        return data.get("genres", [])

    async def discover(
        self,
        media_type: str,
        *,
        page: int = 1,
        sort_by: str = "popularity.desc",
        vote_count_gte: int = 100,
    ) -> list[dict[str, Any]]:
        data = await self._get(
            f"/discover/{media_type}",
            {
                "page": page,
                "sort_by": sort_by,
                "vote_count.gte": vote_count_gte,
                "include_adult": "false",
            },
        )
        return data.get("results", [])

    async def get_movie(self, tmdb_id: int) -> dict[str, Any]:
        return await self._get(
            f"/movie/{tmdb_id}",
            {"append_to_response": "credits,keywords"},
        )

    async def get_tv(self, tmdb_id: int) -> dict[str, Any]:
        return await self._get(
            f"/tv/{tmdb_id}",
            {"append_to_response": "credits,keywords"},
        )

    async def get_watch_providers(self, media_type: str, tmdb_id: int) -> dict[str, Any]:
        """Availability by country from TMDb (JustWatch-sourced).

        ``media_type`` must be ``movie`` or ``tv``.
        """
        if media_type not in {"movie", "tv"}:
            raise ValueError(f"Unsupported media_type for watch providers: {media_type}")
        return await self._get(f"/{media_type}/{tmdb_id}/watch/providers")
