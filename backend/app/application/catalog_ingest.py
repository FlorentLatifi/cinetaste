"""TMDb → catalog ingestion.

Design:
* Title IDs come from the curated onboarding seed deck plus TMDb discover
  lists (most popular *and* most voted, so the catalog mixes what's current
  with well-known titles users can actually rate).
* Details are fetched concurrently (bounded) and written in batches, one
  commit per batch. Each title is upserted in a savepoint, so one bad payload
  or a TMDb error skips that title instead of rolling back the whole run.
* A dropped connection is *not* a bad payload and must not be treated as one.
  It is retried once on a fresh connection, and if that fails too the run stops
  with a single clear error. See ``_connection_is_lost``.
* Re-running is safe: titles are upserted by TMDb id.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    InterfaceError,
    PendingRollbackError,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.onboarding_seed import all_seed_tmdb_ids
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

DETAIL_CONCURRENCY = 8
BATCH_SIZE = 25
DISCOVER_SORTS = ("popularity.desc", "vote_count.desc")
MAX_KEYWORDS = 25
MAX_CAST = 8
CREW_JOBS = {"Director", "Writer", "Screenplay"}


class IngestConnectionLost(RuntimeError):
    """The database became unreachable, so the run stopped early.

    Distinct from a title that fails to upsert: that costs one title, this
    costs every title after it. Batches committed before the drop are safe --
    the ingest is idempotent, so re-running continues from the catalog as it
    stands.
    """


def _connection_is_lost(exc: BaseException) -> bool:
    """Did the session die, or did just this one title fail?

    Worth the care: the two arrive at the same ``except`` and want opposite
    handling. A run against a remote database once hit a dropped socket
    mid-batch and logged nine consecutive tracebacks -- the same dead
    connection, reported once per remaining title -- because the bad-payload
    path swallowed it.

    ``PendingRollbackError`` is the unambiguous case: SQLAlchemy refuses every
    further statement until the transaction is rolled back, so no title after
    it can succeed. The other checks catch the *first* failure, which arrives
    as whatever the driver raised. asyncpg errors are deliberately not matched
    by type -- importing the driver into the application layer to catch one
    would be the wrong trade -- so the cause chain is inspected by name, which
    also covers the bare ``OSError`` a half-open socket surfaces.
    """
    if isinstance(exc, (PendingRollbackError, DisconnectionError, InterfaceError)):
        return True
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True

    driver_connection_errors = {
        "ConnectionDoesNotExistError",
        "ConnectionFailureError",
        "ConnectionRejectionError",
        "ClientCannotConnectError",
        "TooManyConnectionsError",
    }
    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    while pending:
        cause = pending.pop()
        if id(cause) in seen:
            continue
        seen.add(id(cause))
        if isinstance(cause, OSError) or type(cause).__name__ in driver_connection_errors:
            return True
        # ``orig`` as well as the raise-from chain: SQLAlchemy keeps the driver
        # exception on the wrapper, and that is where the useful name lives.
        for nxt in (cause.__cause__, cause.__context__, getattr(cause, "orig", None)):
            if isinstance(nxt, BaseException):
                pending.append(nxt)
    return False


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


class CatalogIngestService:
    def __init__(
        self,
        session: AsyncSession,
        tmdb: TmdbClient,
        *,
        concurrency: int = DETAIL_CONCURRENCY,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self._session = session
        self._tmdb = tmdb
        self._concurrency = max(1, concurrency)
        self._batch_size = max(1, batch_size)
        self._genre_cache: dict[int, Genre] = {}
        self._person_cache: dict[int, Person] = {}
        self._keyword_cache: dict[int, Keyword] = {}
        self._genres_ready = False

    # ------------------------------------------------------------ entrypoints

    async def ingest_onboarding_seed(self) -> dict[str, int]:
        """Upsert the curated onboarding movies (cold start is not pure popularity)."""
        await self._ensure_genres()
        seed_ids = all_seed_tmdb_ids()
        stats = await self._ingest_ids([("movie", tmdb_id) for tmdb_id in seed_ids])
        return {"seed_requested": len(seed_ids), **stats}

    async def ingest_popular(self, *, pages: int = 10, include_tv: bool = True) -> dict[str, Any]:
        """Seed deck + ``pages`` discover pages per sort order and media type."""
        await self._ensure_genres()
        seed_stats = await self.ingest_onboarding_seed()

        targets: dict[tuple[str, int], None] = {}
        for media_type in ("movie", "tv") if include_tv else ("movie",):
            for sort_by in DISCOVER_SORTS:
                for page in range(1, pages + 1):
                    try:
                        results = await self._tmdb.discover(media_type, page=page, sort_by=sort_by)
                    except Exception:
                        logger.exception(
                            "tmdb_discover_failed media_type=%s sort=%s page=%s",
                            media_type,
                            sort_by,
                            page,
                        )
                        break
                    if not results:
                        break
                    for item in results:
                        if item.get("id"):
                            targets[(media_type, int(item["id"]))] = None

        stats = await self._ingest_ids(list(targets))
        return {"onboarding_seed": seed_stats, "discovered": len(targets), **stats}

    async def upsert_movie(self, tmdb_id: int) -> str:
        await self._ensure_genres()
        return await self._upsert_from_detail("movie", await self._tmdb.get_movie(tmdb_id))

    async def upsert_tv(self, tmdb_id: int) -> str:
        await self._ensure_genres()
        return await self._upsert_from_detail("tv", await self._tmdb.get_tv(tmdb_id))

    # ------------------------------------------------------------- batch loop

    async def _ingest_ids(self, targets: list[tuple[str, int]]) -> dict[str, int]:
        created = updated = failed = 0
        semaphore = asyncio.Semaphore(self._concurrency)

        async def fetch(media_type: str, tmdb_id: int) -> tuple[str, int, dict[str, Any] | None]:
            async with semaphore:
                try:
                    if media_type == "movie":
                        return media_type, tmdb_id, await self._tmdb.get_movie(tmdb_id)
                    return media_type, tmdb_id, await self._tmdb.get_tv(tmdb_id)
                except Exception as exc:  # noqa: BLE001 — one title must not stop the run
                    logger.warning(
                        "tmdb_fetch_failed media_type=%s tmdb_id=%s error=%s",
                        media_type,
                        tmdb_id,
                        type(exc).__name__,
                    )
                    return media_type, tmdb_id, None

        for start in range(0, len(targets), self._batch_size):
            batch = targets[start : start + self._batch_size]
            fetched = await asyncio.gather(*(fetch(m, t) for m, t in batch))
            try:
                batch_created, batch_updated, batch_failed = await self._write_batch(fetched)
            except IngestConnectionLost:
                # One retry, because the usual cause is a laptop that slept or a
                # link that blinked: by the time we notice, the network is
                # generally back and the pool hands us a working connection.
                logger.warning(
                    "ingest_connection_lost done=%s/%s - reconnecting and redoing this batch",
                    start,
                    len(targets),
                    exc_info=True,
                )
                await self._reset_session()
                try:
                    batch_created, batch_updated, batch_failed = await self._write_batch(fetched)
                except IngestConnectionLost as lost:
                    raise IngestConnectionLost(
                        f"database connection lost after {created + updated} titles "
                        f"({start} of {len(targets)} attempted). Everything committed "
                        f"so far is safe; re-run to continue."
                    ) from lost

            # Folded in only once the batch is through, so a redone batch
            # replaces its counts instead of doubling them.
            created += batch_created
            updated += batch_updated
            failed += batch_failed
            logger.info(
                "ingest_progress done=%s/%s created=%s updated=%s failed=%s",
                min(start + self._batch_size, len(targets)),
                len(targets),
                created,
                updated,
                failed,
            )
        return {"requested": len(targets), "created": created, "updated": updated, "failed": failed}

    async def _write_batch(
        self, fetched: list[tuple[str, int, dict[str, Any] | None]]
    ) -> tuple[int, int, int]:
        """Upsert one batch and commit it, returning its own (created, updated, failed).

        Counts are per batch rather than accumulated so the caller can redo a
        batch after a reconnect without double counting. Raises
        ``IngestConnectionLost`` the moment the session stops being usable;
        every other failure costs one title and the batch carries on.
        """
        created = updated = failed = 0
        for media_type, tmdb_id, payload in fetched:
            if payload is None:
                failed += 1
                continue
            try:
                async with self._session.begin_nested():
                    status = await self._upsert_from_detail(media_type, payload)
            except Exception as exc:
                if _connection_is_lost(exc):
                    raise IngestConnectionLost(str(exc)) from exc
                failed += 1
                # Objects created in the rolled-back savepoint are gone; drop
                # cached references so later titles look them up again.
                self._forget_cached_rows()
                logger.exception("title_upsert_failed media_type=%s tmdb_id=%s", media_type, tmdb_id)
                continue
            created += status == "created"
            updated += status == "updated"

        try:
            await self._session.commit()
        except Exception as exc:
            if _connection_is_lost(exc):
                raise IngestConnectionLost(str(exc)) from exc
            raise
        return created, updated, failed

    async def _reset_session(self) -> None:
        """Make the session usable again after the connection dropped.

        The rollback is what clears the failed transaction; the next statement
        then checks a fresh connection out of the pool. It can itself fail on a
        dead socket, which is fine and not worth propagating - the state it was
        meant to clear is gone either way.
        """
        try:
            await self._session.rollback()
        except Exception:  # noqa: BLE001 - best effort on an already-dead session
            logger.warning("ingest_rollback_failed", exc_info=True)
        self._forget_cached_rows()

    def _forget_cached_rows(self) -> None:
        """Drop ORM rows cached from a transaction that no longer exists.

        ``_genres_ready`` deliberately stays set: ``_ensure_genres`` commits, so
        the genre rows survive and the lookups below find them again by SELECT.
        Anything that was *not* committed is gone from the database too, and is
        simply recreated.
        """
        self._genre_cache.clear()
        self._person_cache.clear()
        self._keyword_cache.clear()

    # ------------------------------------------------------------ lookups

    async def _ensure_genres(self) -> None:
        if self._genres_ready:
            return
        for media_type in ("movie", "tv"):
            for item in await self._tmdb.get_genres(media_type):
                await self._get_or_create_genre(item["id"], item["name"])
        await self._session.commit()
        self._genres_ready = True

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
            person = Person(id=uuid4(), name=name, external_tmdb_id=tmdb_id, profile_path=profile_path)
            self._session.add(person)
            await self._session.flush()
        self._person_cache[tmdb_id] = person
        return person

    async def _get_or_create_keyword(self, tmdb_id: int, name: str) -> Keyword:
        if tmdb_id in self._keyword_cache:
            return self._keyword_cache[tmdb_id]
        keyword = await self._session.scalar(select(Keyword).where(Keyword.external_tmdb_id == tmdb_id))
        if keyword is None:
            keyword = await self._session.scalar(select(Keyword).where(Keyword.name == name))
        if keyword is None:
            keyword = Keyword(id=uuid4(), name=name, external_tmdb_id=tmdb_id)
            self._session.add(keyword)
            await self._session.flush()
        self._keyword_cache[tmdb_id] = keyword
        return keyword

    # ------------------------------------------------------------- upsert

    async def _upsert_from_detail(self, media_type: str, payload: dict[str, Any]) -> str:
        tmdb_id = int(payload["id"])
        existing = await self._session.scalar(select(Title).where(Title.external_tmdb_id == tmdb_id))

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
            episode_run_times = payload.get("episode_run_time") or []
            runtime = int(episode_run_times[0]) if episode_run_times else None
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

        # Replace join rows wholesale: simple and correct for re-ingest.
        await self._session.execute(delete(TitleGenre).where(TitleGenre.title_id == title.id))
        await self._session.execute(delete(TitleKeyword).where(TitleKeyword.title_id == title.id))
        await self._session.execute(delete(Credit).where(Credit.title_id == title.id))

        genre_names: list[str] = []
        for g in payload.get("genres") or []:
            genre = await self._get_or_create_genre(int(g["id"]), g["name"])
            self._session.add(TitleGenre(title_id=title.id, genre_id=genre.id))
            genre_names.append(genre.name)

        keyword_names: list[str] = []
        for k in keyword_items[:MAX_KEYWORDS]:
            keyword = await self._get_or_create_keyword(int(k["id"]), k["name"])
            self._session.add(TitleKeyword(title_id=title.id, keyword_id=keyword.id))
            keyword_names.append(keyword.name)

        people_signals = await self._add_credits(title, payload)
        countries = self._countries(payload)

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
            original_language=title.original_language,
            countries=countries,
        )
        title.embedding = embedding
        title.extra = meta
        await self._session.flush()
        logger.debug("%s title tmdb_id=%s", status, tmdb_id)
        return status

    async def _add_credits(self, title: Title, payload: dict[str, Any]) -> list[PersonSignal]:
        credits = payload.get("credits") or {}
        signals: list[PersonSignal] = []
        seen: set[tuple[int, str, str | None]] = set()

        def add_credit(person: Person, credit_type: str, job: str | None, **extra: Any) -> bool:
            key = (int(person.external_tmdb_id or 0), credit_type, job)
            if key in seen:  # uq_credit_identity: same person twice in one role
                return False
            seen.add(key)
            self._session.add(
                Credit(
                    id=uuid4(),
                    title_id=title.id,
                    person_id=person.id,
                    credit_type=credit_type,
                    job=job,
                    **extra,
                )
            )
            return True

        for cast in (credits.get("cast") or [])[:MAX_CAST]:
            if not cast.get("id") or not cast.get("name"):
                continue
            person = await self._get_or_create_person(int(cast["id"]), cast["name"], cast.get("profile_path"))
            order = cast.get("order")
            billing = int(order) if order is not None else None
            if add_credit(person, "cast", None, character=cast.get("character"), billing_order=billing):
                signals.append(PersonSignal(name=person.name, role="cast", billing_order=billing))

        for crew in credits.get("crew") or []:
            job = (crew.get("job") or "").strip()
            if job not in CREW_JOBS or not crew.get("id") or not crew.get("name"):
                continue
            person = await self._get_or_create_person(int(crew["id"]), crew["name"], crew.get("profile_path"))
            if add_credit(person, "crew", job):
                signals.append(
                    PersonSignal(name=person.name, role="director" if job == "Director" else "writer")
                )

        # TV creators live in `created_by`, not in the crew list. They are the
        # closest analogue to a film's writer-director, so they feed the writer
        # signal (and show as "Creator" on the detail page).
        for creator in payload.get("created_by") or []:
            if not creator.get("id") or not creator.get("name"):
                continue
            person = await self._get_or_create_person(
                int(creator["id"]), creator["name"], creator.get("profile_path")
            )
            if add_credit(person, "crew", "Creator"):
                signals.append(PersonSignal(name=person.name, role="writer"))
        return signals

    @staticmethod
    def _countries(payload: dict[str, Any]) -> list[str]:
        """Production countries (ISO 3166-1); TV falls back to origin_country."""
        countries: list[str] = []
        for c in payload.get("production_countries") or []:
            code = (c.get("iso_3166_1") or "").strip().upper()
            if code and code not in countries:
                countries.append(code)
        if not countries:
            for code in payload.get("origin_country") or []:
                c = str(code).strip().upper()
                if c and c not in countries:
                    countries.append(c)
        return countries[:3]
