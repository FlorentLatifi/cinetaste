from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import Settings
from app.domain.exceptions import AppError

logger = logging.getLogger(__name__)


class TmdbClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.tmdb_api_key:
            raise AppError(
                "TMDB_API_KEY is not configured. Get a free key at "
                "https://www.themoviedb.org/settings/api",
                status_code=503,
                code="tmdb_not_configured",
            )
        self._base = settings.tmdb_base_url.rstrip("/")
        self._key = settings.tmdb_api_key
        self._client = httpx.AsyncClient(timeout=30.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = {"api_key": self._key, **(params or {})}
        url = f"{self._base}{path}"
        response = await self._client.get(url, params=query)
        if response.status_code == 401:
            raise AppError("Invalid TMDB API key", status_code=503, code="tmdb_unauthorized")
        if response.status_code == 429:
            raise AppError("TMDB rate limit exceeded", status_code=503, code="tmdb_rate_limited")
        response.raise_for_status()
        return response.json()

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

