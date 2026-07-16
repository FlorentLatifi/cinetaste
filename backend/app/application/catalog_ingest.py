from __future__ import annotations

import logging
from datetime import date
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.infrastructure.db.models.catalog import (
    Credit,
    Genre,
    Keyword,
    Person,
    Title,
    TitleGenre,
    TitleKeyword,
)
from app.infrastructure.tmdb.client import TmdbClient
from app.recommendation.embeddings import PersonSignal, build_title_signals

logger = logging.getLogger(__name__)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


class CatalogIngestService:
    def __init__(self, session: AsyncSession, tmdb: TmdbClient) -> None:
        self._session = session
        self._tmdb = tmdb
        self._genre_cache: dict[int, Genre] = {}
        self._person_cache: dict[int, Person] = {}
        self._keyword_cache: dict[int, Keyword] = {}

    async def ingest_onboarding_seed(self) -> dict[str, int]:
        """Upsert curated onboarding movies so cold-start is not pure popularity.

        Always run before (or with) discover-based ingest. IDs live in
        ``app/data/onboarding_seed_deck.json``.
        """
        from app.data.onboarding_seed import all_seed_tmdb_ids

        await self._ensure_genres()
        seed_ids = all_seed_tmdb_ids()
        created = updated = errors = 0
        for tmdb_id in seed_ids:
            try:
                status = await self.upsert_movie(tmdb_id)
                created += status == "created"
                updated += status == "updated"
            except Exception:  # noqa: BLE001 — keep seed ingest resilient
                logger.exception("Failed to ingest onboarding seed tmdb_id=%s", tmdb_id)
                errors += 1
        await self._session.commit()
        return {
            "seed_requested": len(seed_ids),
            "created": created,
            "updated": updated,
            "errors": errors,
        }

    async def ingest_popular(self, *, pages: int = 3, include_tv: bool = True) -> dict[str, int]:
        await self._ensure_genres()
        # Curated cold-start titles first (may not appear on today's popular chart).
        seed_stats = await self.ingest_onboarding_seed()

        movie_ids: list[int] = []
        tv_ids: list[int] = []

        for page in range(1, pages + 1):
            results = await self._tmdb.discover("movie", page=page)
            movie_ids.extend(item["id"] for item in results if item.get("id"))
            if include_tv:
                tv_results = await self._tmdb.discover("tv", page=page)
                tv_ids.extend(item["id"] for item in tv_results if item.get("id"))

        created = updated = 0
        for tmdb_id in dict.fromkeys(movie_ids):
            status = await self.upsert_movie(tmdb_id)
            created += status == "created"
            updated += status == "updated"

        for tmdb_id in dict.fromkeys(tv_ids):
            status = await self.upsert_tv(tmdb_id)
            created += status == "created"
            updated += status == "updated"

        await self._session.commit()
        return {
            "onboarding_seed": seed_stats,
            "movies_seen": len(set(movie_ids)),
            "tv_seen": len(set(tv_ids)),
            "created": created,
            "updated": updated,
        }

    async def upsert_movie(self, tmdb_id: int) -> str:
        payload = await self._tmdb.get_movie(tmdb_id)
        return await self._upsert_from_detail("movie", payload)

    async def upsert_tv(self, tmdb_id: int) -> str:
        payload = await self._tmdb.get_tv(tmdb_id)
        return await self._upsert_from_detail("tv", payload)

    async def _ensure_genres(self) -> None:
        for media_type in ("movie", "tv"):
            for item in await self._tmdb.get_genres(media_type):
                await self._get_or_create_genre(item["id"], item["name"])

    async def _get_or_create_genre(self, tmdb_id: int, name: str) -> Genre:
        if tmdb_id in self._genre_cache:
            return self._genre_cache[tmdb_id]
        genre = await self._session.scalar(select(Genre).where(Genre.external_tmdb_id == tmdb_id))
        if genre is None:
            genre = await self._session.scalar(select(Genre).where(Genre.name == name))
        if genre is None:
            genre = Genre(id=uuid4(), name=name, external_tmdb_id=tmdb_id)
            self._session.add(genre)
            await self._session.flush()
        elif genre.external_tmdb_id is None:
            genre.external_tmdb_id = tmdb_id
        self._genre_cache[tmdb_id] = genre
        return genre

    async def _get_or_create_person(self, tmdb_id: int, name: str, profile_path: str | None) -> Person:
        if tmdb_id in self._person_cache:
            return self._person_cache[tmdb_id]
        person = await self._session.scalar(select(Person).where(Person.external_tmdb_id == tmdb_id))
        if person is None:
            person = Person(
                id=uuid4(),
                name=name,
                external_tmdb_id=tmdb_id,
                profile_path=profile_path,
            )
            self._session.add(person)
            await self._session.flush()
        self._person_cache[tmdb_id] = person
        return person

    async def _get_or_create_keyword(self, tmdb_id: int, name: str) -> Keyword:
        if tmdb_id in self._keyword_cache:
            return self._keyword_cache[tmdb_id]
        keyword = await self._session.scalar(
            select(Keyword).where(Keyword.external_tmdb_id == tmdb_id)
        )
        if keyword is None:
            keyword = await self._session.scalar(select(Keyword).where(Keyword.name == name))
        if keyword is None:
            keyword = Keyword(id=uuid4(), name=name, external_tmdb_id=tmdb_id)
            self._session.add(keyword)
            await self._session.flush()
        self._keyword_cache[tmdb_id] = keyword
        return keyword

    async def _upsert_from_detail(self, media_type: str, payload: dict[str, Any]) -> str:
        tmdb_id = int(payload["id"])
        existing = await self._session.scalar(
            select(Title)
            .where(Title.external_tmdb_id == tmdb_id)
            .options(
                selectinload(Title.genres),
                selectinload(Title.keywords),
                selectinload(Title.credits),
            )
        )

        if media_type == "movie":
            name = payload.get("title") or payload.get("original_title") or f"Movie {tmdb_id}"
            original_name = payload.get("original_title")
            release = _parse_date(payload.get("release_date"))
            runtime = payload.get("runtime")
            keyword_items = (payload.get("keywords") or {}).get("keywords") or []
        else:
            name = payload.get("name") or payload.get("original_name") or f"TV {tmdb_id}"
            original_name = payload.get("original_name")
            release = _parse_date(payload.get("first_air_date"))
            runtime = None
            episode_run_times = payload.get("episode_run_time") or []
            if episode_run_times:
                runtime = int(episode_run_times[0])
            keyword_items = (payload.get("keywords") or {}).get("results") or []

        status = "updated" if existing else "created"
        title = existing or Title(id=uuid4(), external_tmdb_id=tmdb_id, media_type=media_type)
        title.media_type = media_type
        title.name = name
        title.original_name = original_name
        title.overview = payload.get("overview")
        title.release_date = release
        title.runtime = runtime
        title.popularity = float(payload.get("popularity") or 0.0)
        title.vote_average = float(payload.get("vote_average") or 0.0)
        title.vote_count = int(payload.get("vote_count") or 0)
        title.poster_path = payload.get("poster_path")
        title.backdrop_path = payload.get("backdrop_path")
        title.original_language = payload.get("original_language")

        if existing is None:
            self._session.add(title)
            await self._session.flush()

        # Replace join rows simply for MVP correctness
        await self._session.execute(delete(TitleGenre).where(TitleGenre.title_id == title.id))
        await self._session.execute(delete(TitleKeyword).where(TitleKeyword.title_id == title.id))
        await self._session.execute(delete(Credit).where(Credit.title_id == title.id))

        genre_names: list[str] = []
        for g in payload.get("genres") or []:
            genre = await self._get_or_create_genre(int(g["id"]), g["name"])
            self._session.add(TitleGenre(title_id=title.id, genre_id=genre.id))
            genre_names.append(genre.name)

        keyword_names: list[str] = []
        for k in keyword_items[:25]:
            keyword = await self._get_or_create_keyword(int(k["id"]), k["name"])
            self._session.add(TitleKeyword(title_id=title.id, keyword_id=keyword.id))
            keyword_names.append(keyword.name)

        credits = payload.get("credits") or {}
        people_signals: list[PersonSignal] = []

        for cast in (credits.get("cast") or [])[:8]:
            if not cast.get("id") or not cast.get("name"):
                continue
            person = await self._get_or_create_person(
                int(cast["id"]), cast["name"], cast.get("profile_path")
            )
            order = cast.get("order")
            billing = int(order) if order is not None else None
            self._session.add(
                Credit(
                    id=uuid4(),
                    title_id=title.id,
                    person_id=person.id,
                    credit_type="cast",
                    job=None,
                    character=cast.get("character"),
                    billing_order=billing,
                )
            )
            people_signals.append(
                PersonSignal(name=person.name, role="cast", billing_order=billing)
            )

        for crew in credits.get("crew") or []:
            job = (crew.get("job") or "").strip()
            if job not in {"Director", "Writer", "Screenplay", "Creator"}:
                continue
            if not crew.get("id") or not crew.get("name"):
                continue
            person = await self._get_or_create_person(
                int(crew["id"]), crew["name"], crew.get("profile_path")
            )
            self._session.add(
                Credit(
                    id=uuid4(),
                    title_id=title.id,
                    person_id=person.id,
                    credit_type="crew",
                    job=job,
                    character=None,
                    billing_order=None,
                )
            )
            role = "director" if job == "Director" else "writer"
            people_signals.append(PersonSignal(name=person.name, role=role))

        # Production countries (ISO 3166-1) for origin taste signal.
        countries: list[str] = []
        for c in payload.get("production_countries") or []:
            code = (c.get("iso_3166_1") or "").strip().upper()
            if code and code not in countries:
                countries.append(code)
            if len(countries) >= 3:
                break
        # TV often uses origin_country list of ISO codes
        if not countries:
            for code in payload.get("origin_country") or []:
                c = str(code).strip().upper()
                if c and c not in countries:
                    countries.append(c)
                if len(countries) >= 3:
                    break

        year = release.year if release else None
        embedding, _features, meta = build_title_signals(
            name=title.name,
            overview=title.overview,
            genres=genre_names,
            keywords=keyword_names,
            people=people_signals,
            media_type=media_type,
            release_year=year,
            runtime=runtime,
            popularity=title.popularity,
            vote_average=title.vote_average,
            original_language=title.original_language,
            countries=countries,
        )
        title.embedding = embedding
        title.extra = meta
        await self._session.flush()
        logger.info("%s title tmdb_id=%s name=%s", status, tmdb_id, title.name)
        return status
