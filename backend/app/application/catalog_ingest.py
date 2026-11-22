"""TMDb → catalog ingestion.

Design:
* Title IDs come from the curated onboarding seed deck plus TMDb discover
  lists (most popular *and* most voted, so the catalog mixes what's current
  with well-known titles users can actually rate).
* Details are fetched concurrently (bounded) and written in batches, one
  commit per batch. A batch is written with a fixed number of statements, not
  a few dozen per title: the run happens far from the database, where every
  statement is a network round trip (see ``_write_titles``). One bad payload
  or a TMDb error skips that title instead of rolling back the whole run; a
  title whose row the database rejects sends its batch back through one
  savepoint per title, so it too costs only itself.
* A dropped connection is *not* a bad payload and must not be treated as one.
  It is retried once on a fresh connection, and if that fails too the run stops
  with a single clear error. See ``_connection_is_lost``.
* Re-running is safe: titles are upserted by TMDb id. Titles that only a
  discovery pass (see ``catalog_discovery``) found are not fetched again once
  they are in the catalog, so an interrupted run resumes cheaply; pass
  ``refresh_existing=True`` to re-fetch everything.
* Every run ends by refreshing catalog-wide popularity / vote-count
  percentiles in ``Title.extra`` (``catalog_percentiles``), which ranking uses
  for "hidden gem" and the cold-start prior.
* A movie's TMDb ``belongs_to_collection`` (its franchise) is kept in
  ``Title.extra["collection"]`` as ``{"id", "name"}`` for the per-collection
  slate cap. It comes with the movie detail call, so it costs no request.
  Titles stored before this existed gain it on ``refresh_existing=True``.
* ``titles.external_tmdb_id`` is unique across movies *and* TV, but TMDb numbers
  the two separately. A TV id that equals a stored movie id is skipped, never
  allowed to overwrite the movie.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import ARRAY, Integer, String, any_, delete, literal, or_, select, update
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    InterfaceError,
    PendingRollbackError,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.catalog_discovery import (
    DISCOVER_SORTS,  # noqa: F401 - re-exported for existing importers
    DiscoveryPlan,
    Throttle,
    build_passes,
    collect_targets,
)
from app.application.catalog_percentiles import refresh_catalog_percentiles
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
from app.recommendation.pipeline import CATALOG_PERCENTILES_KEY, COLLECTION_KEY

logger = logging.getLogger(__name__)

DETAIL_CONCURRENCY = 8
BATCH_SIZE = 25
EXISTING_LOOKUP_CHUNK = 1000
MAX_KEYWORDS = 25
MAX_CAST = 8
CREW_JOBS = {"Director", "Writer", "Screenplay"}
# credits.character is varchar(255). An anthology film lists every segment role
# in one string (TMDb 45005: over 500 characters), and the insert failing cost
# the whole film. The role is display text only, so it is cut to fit instead.
CHARACTER_MAX_LENGTH: int = Credit.__table__.c.character.type.length  # type: ignore[attr-defined]


class IngestConnectionLost(RuntimeError):
    """The database became unreachable, so the run stopped early.

    Distinct from a title that fails to upsert: that costs one title, this
    costs every title after it. Batches committed before the drop are safe --
    the ingest is idempotent, so re-running continues from the catalog as it
    stands.
    """


class IdCollision(RuntimeError):
    """A TMDb id already stored under the other media type."""


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


def _fit_character(value: Any) -> Any:
    """A cast member's role, cut to the column with a trailing "…" when TMDb's is longer."""
    if isinstance(value, str) and len(value) > CHARACTER_MAX_LENGTH:
        return value[: CHARACTER_MAX_LENGTH - 1].rstrip() + "…"
    return value


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def tmdb_collection(raw: Any) -> dict[str, Any] | None:
    """``{"id", "name"}`` from TMDb ``belongs_to_collection``; None if absent or malformed.

    What ingest stores in ``Title.extra["collection"]`` for the franchise cap;
    offline evaluation applies the same rule to its cached payloads.
    """
    if not isinstance(raw, dict):
        return None
    try:
        collection_id = int(raw["id"])
    except (KeyError, TypeError, ValueError):
        return None
    if collection_id <= 0:
        return None
    name = str(raw.get("name") or "").strip()
    return {"id": collection_id, "name": name or None}


def _int_array(values: list[int]) -> Any:
    return literal(values, ARRAY(Integer))


def _text_array(values: list[str]) -> Any:
    return literal(values, ARRAY(String))


def _uuid_array(values: list[UUID]) -> Any:
    return literal(values, ARRAY(PG_UUID(as_uuid=True)))


@dataclass(frozen=True, slots=True)
class _CreditSpec:
    """One cast / crew / creator entry as the payload states it."""

    tmdb_id: int
    name: str
    profile_path: str | None
    credit_type: str  # cast | crew
    job: str | None
    signal_role: str  # cast | director | writer
    character: str | None = None
    billing_order: int | None = None


@dataclass(frozen=True, slots=True)
class _Detail:
    """Everything ingest reads from one TMDb detail payload, read once.

    ``parse`` raises on a malformed payload, before anything touches the
    database, which is what lets a bad payload cost only its own title in a
    batch that is otherwise written in one go.
    """

    media_type: str
    tmdb_id: int
    name: str
    original_name: str | None
    overview: str | None
    release_date: date | None
    runtime: Any
    popularity: float
    vote_average: float
    vote_count: int
    poster_path: str | None
    backdrop_path: str | None
    original_language: str | None
    genres: list[tuple[int, str]]
    keywords: list[tuple[int, str]]
    credits: list[_CreditSpec]
    countries: list[str]
    has_collection_field: bool
    collection: dict[str, Any] | None

    @classmethod
    def parse(cls, media_type: str, payload: dict[str, Any]) -> _Detail:
        tmdb_id = int(payload["id"])
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

        credits_payload = payload.get("credits") or {}
        credits: list[_CreditSpec] = []
        for cast in (credits_payload.get("cast") or [])[:MAX_CAST]:
            if not cast.get("id") or not cast.get("name"):
                continue
            order = cast.get("order")
            credits.append(
                _CreditSpec(
                    tmdb_id=int(cast["id"]),
                    name=cast["name"],
                    profile_path=cast.get("profile_path"),
                    credit_type="cast",
                    job=None,
                    signal_role="cast",
                    character=_fit_character(cast.get("character")),
                    billing_order=int(order) if order is not None else None,
                )
            )
        for crew in credits_payload.get("crew") or []:
            job = (crew.get("job") or "").strip()
            if job not in CREW_JOBS or not crew.get("id") or not crew.get("name"):
                continue
            credits.append(
                _CreditSpec(
                    tmdb_id=int(crew["id"]),
                    name=crew["name"],
                    profile_path=crew.get("profile_path"),
                    credit_type="crew",
                    job=job,
                    signal_role="director" if job == "Director" else "writer",
                )
            )
        # TV creators live in `created_by`, not in the crew list. They are the
        # closest analogue to a film's writer-director, so they feed the writer
        # signal (and show as "Creator" on the detail page).
        for creator in payload.get("created_by") or []:
            if not creator.get("id") or not creator.get("name"):
                continue
            credits.append(
                _CreditSpec(
                    tmdb_id=int(creator["id"]),
                    name=creator["name"],
                    profile_path=creator.get("profile_path"),
                    credit_type="crew",
                    job="Creator",
                    signal_role="writer",
                )
            )

        return cls(
            media_type=media_type,
            tmdb_id=tmdb_id,
            name=name,
            original_name=original_name,
            overview=payload.get("overview"),
            release_date=release,
            runtime=runtime,
            popularity=float(payload.get("popularity") or 0.0),
            vote_average=float(payload.get("vote_average") or 0.0),
            vote_count=int(payload.get("vote_count") or 0),
            poster_path=payload.get("poster_path"),
            backdrop_path=payload.get("backdrop_path"),
            original_language=payload.get("original_language"),
            genres=[(int(g["id"]), g["name"]) for g in payload.get("genres") or []],
            keywords=[(int(k["id"]), k["name"]) for k in keyword_items[:MAX_KEYWORDS]],
            credits=credits,
            countries=CatalogIngestService._countries(payload),
            has_collection_field="belongs_to_collection" in payload,
            collection=tmdb_collection(payload.get("belongs_to_collection")),
        )

    def columns(self) -> dict[str, Any]:
        """The title's own columns, as stored (embedding and extra are added later)."""
        return {
            "media_type": self.media_type,
            "name": self.name,
            "original_name": self.original_name,
            "overview": self.overview,
            "release_date": self.release_date,
            "runtime": self.runtime,
            "popularity": self.popularity,
            "vote_average": self.vote_average,
            "vote_count": self.vote_count,
            "poster_path": self.poster_path,
            "backdrop_path": self.backdrop_path,
            "original_language": self.original_language,
        }


@dataclass(slots=True)
class _Staged:
    """A title resolved in memory, waiting for its join rows."""

    title_id: UUID
    tmdb_id: int
    created: bool
    genres: list[Genre]
    keywords: list[Keyword]
    credits: list[tuple[Person, _CreditSpec]]


class CatalogIngestService:
    def __init__(
        self,
        session: AsyncSession,
        tmdb: TmdbClient,
        *,
        concurrency: int = DETAIL_CONCURRENCY,
        batch_size: int = BATCH_SIZE,
        max_rps: float = 0.0,
    ) -> None:
        self._session = session
        self._tmdb = tmdb
        self._throttle = Throttle(max_rps)
        self._concurrency = max(1, concurrency)
        self._batch_size = max(1, batch_size)
        # TMDb id -> row, for rows that exist in the database or in the open
        # transaction. Names index the same rows for the by-name fallback.
        self._genre_cache: dict[int, Genre] = {}
        self._genre_by_name: dict[str, Genre] = {}
        self._person_cache: dict[int, Person] = {}
        self._keyword_cache: dict[int, Keyword] = {}
        self._keyword_by_name: dict[str, Keyword] = {}
        self._genres_ready = False

    # ------------------------------------------------------------ entrypoints

    async def ingest_onboarding_seed(self) -> dict[str, int]:
        """Upsert the curated onboarding movies (cold start is not pure popularity)."""
        await self._ensure_genres()
        seed_ids = all_seed_tmdb_ids()
        stats = await self._ingest_ids([("movie", tmdb_id) for tmdb_id in seed_ids])
        await self._refresh_percentiles()
        return {"seed_requested": len(seed_ids), **stats}

    async def ingest_popular(
        self,
        *,
        pages: int = 10,
        include_tv: bool = True,
        discovery: DiscoveryPlan | None = None,
        refresh_existing: bool = False,
    ) -> dict[str, Any]:
        """Seed deck + mainstream discover pages + (optionally) discovery passes.

        ``discovery=None`` keeps the original behaviour: ``pages`` pages of
        popularity and vote-count discover per media type.
        """
        await self._ensure_genres()
        seed_stats = await self.ingest_onboarding_seed()

        passes = build_passes(pages=pages, include_tv=include_tv, plan=discovery)
        collected = await collect_targets(self._tmdb, passes, throttle=self._throttle)

        to_fetch, skipped, collisions = await self._plan_fetch(collected.items, refresh_existing)
        stats = await self._ingest_ids(to_fetch)
        await self._refresh_percentiles()
        return {
            "onboarding_seed": seed_stats,
            "discovered": len(collected.items),
            "skipped_existing": skipped,
            "id_collisions": collisions,
            "discover_requests": collected.discover_requests,
            "failed_passes": collected.failed_passes,
            **stats,
        }

    async def _refresh_percentiles(self) -> None:
        """Recompute catalog percentiles after a run; never fails the ingest.

        Ranking falls back to candidate-pool percentiles for titles without
        them, so a failure here costs accuracy, not availability.
        """
        try:
            await refresh_catalog_percentiles(self._session)
            await self._session.commit()
        except Exception:  # noqa: BLE001 - titles are already committed
            logger.warning(
                "catalog_percentiles_refresh_failed - run: "
                "python -m app.scripts.reembed_catalog --percentiles-only",
                exc_info=True,
            )
            await self._reset_session()

    async def _plan_fetch(
        self, items: dict[tuple[str, int], bool], refresh_existing: bool
    ) -> tuple[list[tuple[str, int]], int, int]:
        """Drop discovery-only titles that are already stored.

        Returns (targets to fetch, skipped as already present, skipped as id
        collisions with the other media type).
        """
        candidates = [
            ident for ident, refresh in items.items() if not refresh_existing and not refresh
        ]
        stored: dict[int, str] = {}
        ids = [tmdb_id for _, tmdb_id in candidates]
        for i in range(0, len(ids), EXISTING_LOOKUP_CHUNK):
            rows = await self._session.execute(
                select(Title.external_tmdb_id, Title.media_type).where(
                    Title.external_tmdb_id.in_(ids[i : i + EXISTING_LOOKUP_CHUNK])
                )
            )
            stored.update({tmdb_id: media for tmdb_id, media in rows.all()})

        to_fetch: list[tuple[str, int]] = []
        skipped = collisions = 0
        candidate_set = set(candidates)
        for ident in items:
            media_type, tmdb_id = ident
            if ident in candidate_set and tmdb_id in stored:
                if stored[tmdb_id] == media_type:
                    skipped += 1
                else:
                    collisions += 1
                    logger.warning(
                        "tmdb_id_collision tmdb_id=%s wanted=%s stored=%s",
                        tmdb_id,
                        media_type,
                        stored[tmdb_id],
                    )
                continue
            to_fetch.append(ident)
        return to_fetch, skipped, collisions

    async def upsert_movie(self, tmdb_id: int) -> str:
        await self._ensure_genres()
        return await self._upsert_from_detail("movie", await self._tmdb.get_movie(tmdb_id))

    async def upsert_tv(self, tmdb_id: int) -> str:
        await self._ensure_genres()
        return await self._upsert_from_detail("tv", await self._tmdb.get_tv(tmdb_id))

    async def ingest_one(self, media_type: str, tmdb_id: int) -> str:
        """Fetch and upsert one title on demand, inside the caller's transaction.

        For a user adding a film the catalog lacks. Unlike ``upsert_movie``
        it skips the genre-list prefetch, which costs two more TMDb calls and
        commits: the title's own payload names its genres and
        ``_write_titles`` creates any that are missing.
        """
        if media_type == "movie":
            payload = await self._tmdb.get_movie(tmdb_id)
        elif media_type == "tv":
            payload = await self._tmdb.get_tv(tmdb_id)
        else:
            raise ValueError(f"Unsupported media_type: {media_type}")
        return await self._upsert_from_detail(media_type, payload)

    # ------------------------------------------------------------- batch loop

    async def _ingest_ids(self, targets: list[tuple[str, int]]) -> dict[str, int]:
        created = updated = failed = 0
        semaphore = asyncio.Semaphore(self._concurrency)

        async def fetch(media_type: str, tmdb_id: int) -> tuple[str, int, dict[str, Any] | None]:
            async with semaphore:
                await self._throttle.wait()
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
            counts: tuple[int, int, int] | None = None
            try:
                counts = await self._write_batch(fetched)
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
            if counts is not None:
                batch_created, batch_updated, batch_failed = counts
            else:
                # Redone here, not inside the ``except``: anything raised in an
                # except block carries the exception being handled as its
                # ``__context__``, ``_connection_is_lost`` follows that chain,
                # and so one bad title in the redone batch read as a second
                # lost connection and stopped the run.
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

        The whole batch is written with a fixed number of statements
        (``_write_titles``) inside one savepoint. If one of those statements
        fails for a reason other than the connection -- one title's value too
        long for its column, say -- the savepoint is rolled back and the batch
        is written again one title at a time, each in its own savepoint, so the
        bad title costs only itself, as it did before batching.

        Counts are per batch rather than accumulated so the caller can redo a
        batch after a reconnect without double counting. Raises
        ``IngestConnectionLost`` the moment the session stops being usable;
        every other failure costs one title and the batch carries on.
        """
        failed = 0
        items: list[tuple[str, int, dict[str, Any]]] = []
        details: list[_Detail] = []
        for media_type, tmdb_id, payload in fetched:
            if payload is None:
                failed += 1
                continue
            # Parsed here, not in the writer: a logged exception keeps its
            # traceback, the traceback keeps its frames and their callers', and
            # the writer's frame holds the batch's ORM rows. Rows pinned that
            # way stay in the session's identity map and go stale.
            try:
                details.append(_Detail.parse(media_type, payload))
            except Exception:
                failed += 1
                logger.exception("title_upsert_failed media_type=%s tmdb_id=%s", media_type, tmdb_id)
                continue
            items.append((media_type, tmdb_id, payload))

        outcomes: list[str | IdCollision] | None = None
        try:
            async with self._session.begin_nested():
                outcomes = await self._write_titles(details)
        except Exception as exc:
            if _connection_is_lost(exc):
                raise IngestConnectionLost(str(exc)) from exc
            logger.warning(
                "ingest_batch_write_failed titles=%s - writing them one at a time",
                len(items),
                exc_info=True,
            )
        if outcomes is None:
            # Outside the ``except`` for the reason given in ``_ingest_ids``.
            # The savepoint took every row it created with it.
            self._forget_cached_rows()
            created, updated, one_by_one_failed = await self._write_one_by_one(items)
            failed += one_by_one_failed
        else:
            created = updated = 0
            for outcome in outcomes:
                if isinstance(outcome, IdCollision):
                    failed += 1
                    logger.warning("title_id_collision %s", outcome)
                else:
                    created += outcome == "created"
                    updated += outcome == "updated"

        try:
            await self._session.commit()
        except Exception as exc:
            if _connection_is_lost(exc):
                raise IngestConnectionLost(str(exc)) from exc
            raise
        return created, updated, failed

    async def _write_one_by_one(
        self, items: list[tuple[str, int, dict[str, Any]]]
    ) -> tuple[int, int, int]:
        """The slow path: one savepoint per title, so a bad row costs only its title."""
        created = updated = failed = 0
        for media_type, tmdb_id, payload in items:
            try:
                async with self._session.begin_nested():
                    status = await self._upsert_from_detail(media_type, payload)
            except IdCollision as exc:
                failed += 1
                logger.warning("title_id_collision %s", exc)
                continue
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
        the genre rows survive and the next batch reads them again. Anything
        that was *not* committed is gone from the database too, and is simply
        recreated.
        """
        self._genre_cache.clear()
        self._genre_by_name.clear()
        self._person_cache.clear()
        self._keyword_cache.clear()
        self._keyword_by_name.clear()

    # ------------------------------------------------------------ lookups
    #
    # Reads happen once per batch, one statement per table, and resolution then
    # runs in memory in payload order. The rules are the ones the per-row
    # SELECTs used to apply: genres and keywords by TMDb id, then by name, then
    # created; people by TMDb id, then created with the first name and profile
    # seen. Stored rows win over the payload (a renamed keyword keeps its stored
    # name), and nothing but a genre's missing TMDb id is ever updated.
    #
    # ``= ANY(:array)`` rather than ``IN (...)``: the statement text stays the
    # same whatever the number of ids, so asyncpg reuses its prepared statement
    # instead of paying a round trip to prepare a new one for every batch.

    async def _ensure_genres(self) -> None:
        if self._genres_ready:
            return
        pairs = [
            (item["id"], item["name"])
            for media_type in ("movie", "tv")
            for item in await self._tmdb.get_genres(media_type)
        ]
        await self._load_genres(pairs)
        for tmdb_id, name in pairs:
            self._genre(tmdb_id, name)
        await self._session.commit()
        self._genres_ready = True

    async def _load_genres(self, pairs: list[tuple[int, str]]) -> None:
        missing = [(i, n) for i, n in pairs if i not in self._genre_cache]
        if not missing:
            return
        rows = await self._session.scalars(
            select(Genre).where(
                or_(
                    Genre.external_tmdb_id == any_(_int_array([i for i, _ in missing])),
                    Genre.name == any_(_text_array([n for _, n in missing])),
                )
            )
        )
        for genre in rows:
            if genre.external_tmdb_id is not None:
                self._genre_cache[genre.external_tmdb_id] = genre
            self._genre_by_name[genre.name] = genre

    async def _load_people(self, tmdb_ids: list[int]) -> None:
        missing = [i for i in dict.fromkeys(tmdb_ids) if i not in self._person_cache]
        if not missing:
            return
        rows = await self._session.scalars(
            select(Person).where(Person.external_tmdb_id == any_(_int_array(missing)))
        )
        for person in rows:
            self._person_cache[person.external_tmdb_id] = person  # type: ignore[index]

    async def _load_keywords(self, pairs: list[tuple[int, str]]) -> None:
        missing = [(i, n) for i, n in pairs if i not in self._keyword_cache]
        if not missing:
            return
        rows = await self._session.scalars(
            select(Keyword).where(
                or_(
                    Keyword.external_tmdb_id == any_(_int_array([i for i, _ in missing])),
                    Keyword.name == any_(_text_array([n for _, n in missing])),
                )
            )
        )
        for keyword in rows:
            if keyword.external_tmdb_id is not None:
                self._keyword_cache[keyword.external_tmdb_id] = keyword
            self._keyword_by_name[keyword.name] = keyword

    def _genre(self, tmdb_id: int, name: str) -> Genre:
        genre = self._genre_cache.get(tmdb_id)
        if genre is not None:
            return genre
        genre = self._genre_by_name.get(name)
        if genre is None:
            genre = Genre(id=uuid4(), name=name, external_tmdb_id=tmdb_id)
            self._session.add(genre)
            self._genre_by_name[name] = genre
        elif genre.external_tmdb_id is None:
            genre.external_tmdb_id = tmdb_id
        self._genre_cache[tmdb_id] = genre
        return genre

    def _person(self, tmdb_id: int, name: str, profile_path: str | None) -> Person:
        person = self._person_cache.get(tmdb_id)
        if person is None:
            person = Person(
                id=uuid4(), name=name, external_tmdb_id=tmdb_id, profile_path=profile_path
            )
            self._session.add(person)
            self._person_cache[tmdb_id] = person
        return person

    def _keyword(self, tmdb_id: int, name: str) -> Keyword:
        keyword = self._keyword_cache.get(tmdb_id) or self._keyword_by_name.get(name)
        if keyword is None:
            keyword = Keyword(id=uuid4(), name=name, external_tmdb_id=tmdb_id)
            self._session.add(keyword)
            self._keyword_by_name[name] = keyword
        self._keyword_cache[tmdb_id] = keyword
        return keyword

    # ------------------------------------------------------------- upsert

    async def _upsert_from_detail(self, media_type: str, payload: dict[str, Any]) -> str:
        """Upsert one title; raises what rejects it (a malformed payload, ``IdCollision``)."""
        [outcome] = await self._write_titles([_Detail.parse(media_type, payload)])
        if isinstance(outcome, IdCollision):
            raise outcome
        return outcome

    async def _write_titles(self, details: list[_Detail]) -> list[str | IdCollision]:
        """Upsert titles with a fixed number of statements, however many there are.

        Returns one outcome per detail, in order: ``"created"``, ``"updated"``,
        or the ``IdCollision`` that rejected it (which writes nothing and does
        not stop the others). A failing *statement* raises instead: run this
        inside a savepoint and roll it back.

        Statements, whatever the number of titles: one SELECT each for titles,
        people and keywords (and genres, only when one is not cached), one
        flush for new people / keywords / titles and title updates, one DELETE
        per join table for titles being replaced, one flush for the join rows.
        The cost of an ingest run against a remote database is round trips, so
        this is what keeps a batch of 25 from taking 25 times as long as one.
        """
        if not details:
            return []
        outcomes: list[str | IdCollision] = []

        # populate_existing: read what is stored, not a copy the session still
        # holds from earlier in the run. The percentile refresh rewrites
        # ``extra`` in SQL, behind the ORM's back, and a title can come round
        # twice in one run (seed deck, then mainstream).
        stored = {
            title.external_tmdb_id: title
            for title in await self._session.scalars(
                select(Title)
                .where(Title.external_tmdb_id == any_(_int_array([d.tmdb_id for d in details])))
                .execution_options(populate_existing=True)
            )
        }
        await self._load_genres([g for d in details for g in d.genres])
        await self._load_people([c.tmdb_id for d in details for c in d.credits])
        await self._load_keywords([k for d in details for k in d.keywords])

        staged: list[_Staged] = []
        updates: list[dict[str, Any]] = []
        claimed: dict[int, str] = {}  # tmdb id -> media type, for titles earlier in this call
        for detail in details:
            existing = stored.get(detail.tmdb_id)
            stored_as = existing.media_type if existing is not None else claimed.get(detail.tmdb_id)
            if stored_as is not None and stored_as != detail.media_type:
                outcomes.append(
                    IdCollision(
                        f"tmdb id {detail.tmdb_id} is stored as {stored_as}, not {detail.media_type}"
                    )
                )
                continue
            claimed[detail.tmdb_id] = detail.media_type
            row, values = self._stage(detail, existing)
            if existing is None:
                self._session.add(Title(id=row.title_id, external_tmdb_id=detail.tmdb_id, **values))
            else:
                updates.append({"id": row.title_id, **values})
            staged.append(row)
            outcomes.append("created" if row.created else "updated")

        # New people, keywords, genres and titles.
        await self._session.flush()
        if updates:
            # One executemany for every stored title. Through the unit of work
            # each would be its own UPDATE: the models fetch ``updated_at`` back
            # with RETURNING (eager_defaults), and that rules out batching. The
            # same columns are written and ``updated_at`` still becomes now().
            await self._session.execute(update(Title), updates)
            # The bulk UPDATE went past the loaded objects; never serve their
            # old values. The next read loads them again.
            for title in stored.values():
                self._session.expire(title)

        # Replace join rows wholesale: simple and correct for re-ingest. New
        # titles have none, so they cost nothing here.
        replaced = [row.title_id for row in staged if not row.created]
        if replaced:
            for model in (TitleGenre, TitleKeyword, Credit):
                await self._session.execute(
                    delete(model).where(model.title_id == any_(_uuid_array(replaced)))
                )
        for row in staged:
            for genre in row.genres:
                self._session.add(TitleGenre(title_id=row.title_id, genre_id=genre.id))
            for keyword in row.keywords:
                self._session.add(TitleKeyword(title_id=row.title_id, keyword_id=keyword.id))
            for person, spec in row.credits:
                self._session.add(
                    Credit(
                        id=uuid4(),
                        title_id=row.title_id,
                        person_id=person.id,
                        credit_type=spec.credit_type,
                        job=spec.job,
                        character=spec.character,
                        billing_order=spec.billing_order,
                    )
                )
        await self._session.flush()
        for row in staged:
            status = "created" if row.created else "updated"
            logger.debug("%s title tmdb_id=%s", status, row.tmdb_id)
        return outcomes

    def _stage(
        self, detail: _Detail, existing: Title | None
    ) -> tuple[_Staged, dict[str, Any]]:
        """Resolve one title's rows in memory and compute its columns; no statements."""
        genres = [self._genre(tmdb_id, name) for tmdb_id, name in detail.genres]
        keywords = [self._keyword(tmdb_id, name) for tmdb_id, name in detail.keywords]

        credits: list[tuple[Person, _CreditSpec]] = []
        people_signals: list[PersonSignal] = []
        seen: set[tuple[int, str, str | None]] = set()
        for spec in detail.credits:
            person = self._person(spec.tmdb_id, spec.name, spec.profile_path)
            key = (int(person.external_tmdb_id or 0), spec.credit_type, spec.job)
            if key in seen:  # uq_credit_identity: same person twice in one role
                continue
            seen.add(key)
            credits.append((person, spec))
            people_signals.append(
                PersonSignal(
                    name=person.name, role=spec.signal_role, billing_order=spec.billing_order
                )
            )

        embedding, _features, meta = build_title_signals(
            name=detail.name,
            overview=detail.overview,
            genres=[g.name for g in genres],
            keywords=[k.name for k in keywords],
            people=people_signals,
            media_type=detail.media_type,
            release_year=detail.release_date.year if detail.release_date else None,
            runtime=detail.runtime,
            original_language=detail.original_language,
            countries=detail.countries,
        )
        # Keep the stored catalog percentiles: they are recomputed for the whole
        # catalog at the end of the run, and until then a stale value beats none.
        previous = (existing.extra or {}) if existing is not None else {}
        if CATALOG_PERCENTILES_KEY in previous:
            meta = {**meta, CATALOG_PERCENTILES_KEY: previous[CATALOG_PERCENTILES_KEY]}
        # A movie payload always carries the field (null when the film stands
        # alone), so it is authoritative; TV payloads have no such field, and a
        # payload without it keeps whatever was stored.
        if detail.has_collection_field:
            collection = detail.collection
        else:
            collection = previous.get(COLLECTION_KEY)
        if collection:
            meta = {**meta, COLLECTION_KEY: collection}

        row = _Staged(
            title_id=existing.id if existing is not None else uuid4(),
            tmdb_id=detail.tmdb_id,
            created=existing is None,
            genres=genres,
            keywords=keywords,
            credits=credits,
        )
        return row, {**detail.columns(), "embedding": embedding, "extra": meta}

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
