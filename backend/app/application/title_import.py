"""Adding a title the catalog does not have yet.

The catalog is a few thousand titles ingested ahead of time. Search only
looked there, so a film someone loves but that never made a discover page
could not be found, rated, or used to shape their taste. This looks it up on
TMDb and ingests it on request, with the same parsing and embedding as the
batch ingest, so it ranks and explains like every other title.

Cost per import: one TMDb detail call and one transaction. Imports are capped
per user, because each one is a TMDb request made on their behalf.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.catalog_ingest import CatalogIngestService, IdCollision
from app.domain.exceptions import ConflictError, RateLimitedError
from app.infrastructure.cache import get_rate_limit_store
from app.infrastructure.db.models.catalog import Title
from app.infrastructure.tmdb.client import TmdbClient

logger = logging.getLogger(__name__)

# Generous for someone adding their favourites, small next to TMDb's quota.
IMPORTS_PER_HOUR = 30
_IMPORT_WINDOW_SECONDS = 3600
EXTERNAL_SEARCH_LIMIT = 12


@dataclass(frozen=True, slots=True)
class ExternalTitle:
    tmdb_id: int
    media_type: str
    name: str
    year: int | None
    overview: str | None
    poster_path: str | None
    # Set when this title is already in the catalog: open it, don't import it.
    title_id: UUID | None


def _year(raw: Any) -> int | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw[:10]).year
    except ValueError:
        return None


def parse_search_result(item: dict[str, Any]) -> ExternalTitle | None:
    """One TMDb multi-search row, or None when it has nothing to show."""
    media_type = item.get("media_type")
    name = item.get("title") if media_type == "movie" else item.get("name")
    if media_type not in {"movie", "tv"} or not name or not isinstance(item.get("id"), int):
        return None
    released = item.get("release_date") if media_type == "movie" else item.get("first_air_date")
    return ExternalTitle(
        tmdb_id=item["id"],
        media_type=media_type,
        name=str(name),
        year=_year(released),
        overview=item.get("overview") or None,
        poster_path=item.get("poster_path") or None,
        title_id=None,
    )


class TitleImportService:
    def __init__(self, session: AsyncSession, tmdb: TmdbClient) -> None:
        self._session = session
        self._tmdb = tmdb

    async def search(self, query: str) -> list[ExternalTitle]:
        """TMDb matches for ``query``, marking the ones already in the catalog."""
        parsed = [
            row
            for row in (parse_search_result(item) for item in await self._tmdb.search_titles(query))
            if row is not None
        ][:EXTERNAL_SEARCH_LIMIT]
        if not parsed:
            return []
        stored = await self._stored([row.tmdb_id for row in parsed])
        out: list[ExternalTitle] = []
        for row in parsed:
            hit = stored.get(row.tmdb_id)
            # Same TMDb id stored as the other media type is a different title.
            title_id = hit[0] if hit is not None and hit[1] == row.media_type else None
            out.append(
                ExternalTitle(
                    tmdb_id=row.tmdb_id,
                    media_type=row.media_type,
                    name=row.name,
                    year=row.year,
                    overview=row.overview,
                    poster_path=row.poster_path,
                    title_id=title_id,
                )
            )
        return out

    async def import_title(self, *, user_id: UUID, media_type: str, tmdb_id: int) -> UUID:
        """The catalog id for this TMDb title, ingesting it first if needed."""
        existing = await self._existing_id(media_type, tmdb_id)
        if existing is not None:
            return existing

        await self._spend_import_budget(user_id)
        ingest = CatalogIngestService(self._session, self._tmdb)
        try:
            # A savepoint, so a title that loses a race with another import (or
            # with the catalog job) costs this one statement, not the request.
            async with self._session.begin_nested():
                await ingest.ingest_one(media_type, tmdb_id)
        except IdCollision as exc:
            raise ConflictError(
                "That title can't be added right now.", code="title_import_conflict"
            ) from exc
        except IntegrityError:
            logger.info("title_import_race media_type=%s tmdb_id=%s", media_type, tmdb_id)

        title_id = await self._existing_id(media_type, tmdb_id)
        if title_id is None:
            raise ConflictError(
                "That title can't be added right now. Try again in a moment.",
                code="title_import_conflict",
            )
        logger.info("title_imported media_type=%s tmdb_id=%s", media_type, tmdb_id)
        return title_id

    async def _existing_id(self, media_type: str, tmdb_id: int) -> UUID | None:
        row = (
            await self._session.execute(
                select(Title.id, Title.media_type).where(Title.external_tmdb_id == tmdb_id)
            )
        ).first()
        if row is None:
            return None
        if row.media_type != media_type:
            raise ConflictError(
                "That title can't be added right now.", code="title_import_conflict"
            )
        return row.id

    async def _stored(self, tmdb_ids: list[int]) -> dict[int, tuple[UUID, str]]:
        rows = await self._session.execute(
            select(Title.external_tmdb_id, Title.id, Title.media_type).where(
                Title.external_tmdb_id.in_(tmdb_ids)
            )
        )
        return {tmdb_id: (title_id, media) for tmdb_id, title_id, media in rows.all()}

    async def _spend_import_budget(self, user_id: UUID) -> None:
        """Count one import against this user; refuse past the hourly cap.

        Fails open: the IP rate limit still applies, and a counter outage
        should not stop someone adding a film.
        """
        try:
            count, retry_after = await get_rate_limit_store().hit(
                f"rl:import:{user_id}", window_seconds=_IMPORT_WINDOW_SECONDS
            )
        except Exception:  # noqa: BLE001
            logger.warning("title_import_budget_unavailable", exc_info=True)
            return
        if count > IMPORTS_PER_HOUR:
            raise RateLimitedError(
                "You've added a lot of titles in the last hour. Try again later.",
                retry_after=retry_after,
            )
