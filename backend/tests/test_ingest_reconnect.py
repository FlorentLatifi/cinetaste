"""Telling a dead connection apart from one bad title.

A real run against the production database dropped its socket mid-batch and
logged nine consecutive tracebacks — the same dead connection, reported once
per remaining title — because the per-title ``except`` treated it like a
payload TMDb had mangled. These tests pin the distinction: a bad payload costs
one title, a dead connection stops the batch and is retried once.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, InterfaceError, PendingRollbackError

from app.application.catalog_ingest import (
    CatalogIngestService,
    IngestConnectionLost,
    _connection_is_lost,
)


class ConnectionDoesNotExistError(Exception):
    """Stand-in for the asyncpg class of the same name.

    Named to match deliberately: the production code identifies driver errors
    by class name rather than importing asyncpg into the application layer, so
    the name is the contract being tested.
    """


def _dropped_socket() -> DBAPIError:
    """What asyncpg produced when the laptop slept mid-INSERT."""
    driver = ConnectionDoesNotExistError("connection was closed in the middle of operation")
    return DBAPIError("INSERT INTO keywords ...", {}, driver)


class FakeSession:
    """Just enough AsyncSession to drive the batch loop."""

    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def begin_nested(self) -> FakeSession:
        return self

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeTmdb:
    async def get_movie(self, tmdb_id: int) -> dict[str, Any]:
        return {"id": tmdb_id}

    async def get_tv(self, tmdb_id: int) -> dict[str, Any]:
        return {"id": tmdb_id}


def _service(session: FakeSession, *, batch_size: int = 25) -> CatalogIngestService:
    return CatalogIngestService(session, FakeTmdb(), batch_size=batch_size)  # type: ignore[arg-type]


# --------------------------------------------------------------- the predicate


def test_a_poisoned_session_is_recognised() -> None:
    """The one that fired nine times. Nothing can run until a rollback."""
    assert _connection_is_lost(PendingRollbackError("Can't reconnect until ..."))


def test_sqlalchemy_saying_the_connection_is_invalid_is_enough() -> None:
    err = DBAPIError("SELECT 1", {}, Exception("boom"), connection_invalidated=True)
    assert _connection_is_lost(err)


def test_the_driver_error_is_found_by_name_through_the_wrapper() -> None:
    """connection_invalidated is not always set; the name still identifies it."""
    assert _connection_is_lost(_dropped_socket())


def test_the_exact_chain_production_produced() -> None:
    """Three levels deep, and ``connection_invalidated`` is False.

    Reconstructed from the failing run:
    ``ConnectionDoesNotExistError`` -> ``AsyncAdapt_asyncpg_dbapi.Error`` ->
    ``DBAPIError``. Only the walk through ``orig`` and then ``__cause__``
    reaches the name, which is why the flag alone is not relied on.
    """

    class Error(Exception):
        """Stand-in for AsyncAdapt_asyncpg_dbapi.Error."""

    root = ConnectionDoesNotExistError("connection was closed in the middle of operation")
    middle = Error("<class 'asyncpg.exceptions.ConnectionDoesNotExistError'>")
    middle.__cause__ = root
    wrapper = DBAPIError("INSERT INTO keywords ...", {}, middle)

    assert wrapper.connection_invalidated is False, "the flag alone would have missed this"
    assert _connection_is_lost(wrapper)


def test_a_bare_oserror_in_the_chain_counts() -> None:
    """WinError 121 — the semaphore timeout a half-open socket surfaces."""
    root = OSError(121, "The semaphore timeout period has expired")
    wrapper = DBAPIError("SELECT 1", {}, Exception("wrapped"))
    wrapper.__cause__ = root
    assert _connection_is_lost(wrapper)


def test_interface_errors_count() -> None:
    assert _connection_is_lost(InterfaceError("SELECT 1", {}, Exception("closed")))


def test_a_mangled_payload_does_not_count() -> None:
    """The case the savepoint exists for: skip the title, keep the run."""
    assert not _connection_is_lost(ValueError("TMDb sent a genre with no id"))
    assert not _connection_is_lost(KeyError("name"))
    assert not _connection_is_lost(
        IntegrityError("INSERT ...", {}, Exception("duplicate key value"))
    )


def test_a_self_referential_cause_chain_terminates() -> None:
    """Cycles are legal in __context__; the walk must not hang on one."""
    a = RuntimeError("a")
    b = RuntimeError("b")
    a.__cause__ = b
    b.__cause__ = a
    assert not _connection_is_lost(a)


# -------------------------------------------------------------- the batch loop


@pytest.mark.asyncio
async def test_a_dead_connection_stops_the_batch_instead_of_burning_it() -> None:
    """The regression. Seven more titles must not be marked failed by one drop."""
    attempted: list[int] = []

    async def upsert(self: object, media_type: str, payload: dict) -> str:
        attempted.append(payload["id"])
        if len(attempted) == 3:
            raise _dropped_socket()
        return "created"

    with patch.object(CatalogIngestService, "_upsert_from_detail", upsert):
        service = _service(FakeSession(), batch_size=10)
        with pytest.raises(IngestConnectionLost):
            await service._write_batch([("movie", i, {"id": i}) for i in range(1, 11)])

    assert attempted == [1, 2, 3], "it marched on past the dead connection"


@pytest.mark.asyncio
async def test_the_batch_is_redone_once_and_counts_are_not_doubled() -> None:
    """Titles 1 and 2 succeed, die on 3, reconnect, redo all four: created == 4."""
    session = FakeSession()
    attempted: list[int] = []
    drops_left = {"n": 1}

    async def upsert(self: object, media_type: str, payload: dict) -> str:
        attempted.append(payload["id"])
        if payload["id"] == 3 and drops_left["n"]:
            drops_left["n"] -= 1
            raise _dropped_socket()
        return "created"

    with patch.object(CatalogIngestService, "_upsert_from_detail", upsert):
        service = _service(session, batch_size=4)
        stats = await service._ingest_ids([("movie", i) for i in (1, 2, 3, 4)])

    assert stats == {"requested": 4, "created": 4, "updated": 0, "failed": 0}
    assert attempted == [1, 2, 3, 1, 2, 3, 4]
    assert session.rollbacks == 1, "the failed transaction was never cleared"


@pytest.mark.asyncio
async def test_a_second_drop_stops_the_run_and_says_what_survived() -> None:
    """No point grinding through 400 more titles against a database that is gone."""
    session = FakeSession()

    async def upsert(self: object, media_type: str, payload: dict) -> str:
        if payload["id"] > 2:
            raise _dropped_socket()
        return "created"

    with patch.object(CatalogIngestService, "_upsert_from_detail", upsert):
        service = _service(session, batch_size=2)
        with pytest.raises(IngestConnectionLost) as err:
            await service._ingest_ids([("movie", i) for i in (1, 2, 3, 4, 5, 6)])

    message = str(err.value)
    assert "after 2 titles" in message, message
    assert "re-run to continue" in message, message


@pytest.mark.asyncio
async def test_a_failing_commit_is_also_a_lost_connection() -> None:
    """The drop can land on the batch commit rather than on a title."""

    class CommitFails(FakeSession):
        async def commit(self) -> None:
            raise _dropped_socket()

    async def upsert(self: object, media_type: str, payload: dict) -> str:
        return "created"

    with patch.object(CatalogIngestService, "_upsert_from_detail", upsert):
        service = _service(CommitFails(), batch_size=2)
        with pytest.raises(IngestConnectionLost):
            await service._ingest_ids([("movie", 1), ("movie", 2)])


@pytest.mark.asyncio
async def test_a_commit_failing_for_any_other_reason_is_not_disguised() -> None:
    """Only connection loss is reclassified. A constraint violation still surfaces.

    Converting every commit failure into IngestConnectionLost would send the
    reader hunting a network problem that was never there.
    """

    class CommitViolates(FakeSession):
        async def commit(self) -> None:
            raise IntegrityError("INSERT ...", {}, Exception("duplicate key value"))

    async def upsert(self: object, media_type: str, payload: dict) -> str:
        return "created"

    with patch.object(CatalogIngestService, "_upsert_from_detail", upsert):
        service = _service(CommitViolates(), batch_size=2)
        with pytest.raises(IntegrityError):
            await service._ingest_ids([("movie", 1), ("movie", 2)])


@pytest.mark.asyncio
async def test_one_bad_payload_still_only_costs_one_title() -> None:
    """The behaviour that was already right, kept right."""

    async def upsert(self: object, media_type: str, payload: dict) -> str:
        if payload["id"] == 2:
            raise ValueError("TMDb sent a genre with no id")
        return "created"

    with patch.object(CatalogIngestService, "_upsert_from_detail", upsert):
        service = _service(FakeSession(), batch_size=4)
        stats = await service._ingest_ids([("movie", i) for i in (1, 2, 3, 4)])

    assert stats == {"requested": 4, "created": 3, "updated": 0, "failed": 1}


@pytest.mark.asyncio
async def test_a_tmdb_fetch_failure_is_counted_once_not_retried() -> None:
    """A title TMDb refuses is a failure of that title, not of the connection."""

    class RefusingTmdb(FakeTmdb):
        async def get_movie(self, tmdb_id: int) -> dict[str, Any]:
            if tmdb_id == 2:
                raise RuntimeError("TMDb rate limit")
            return {"id": tmdb_id}

    async def upsert(self: object, media_type: str, payload: dict) -> str:
        return "created"

    with patch.object(CatalogIngestService, "_upsert_from_detail", upsert):
        service = CatalogIngestService(FakeSession(), RefusingTmdb(), batch_size=4)  # type: ignore[arg-type]
        stats = await service._ingest_ids([("movie", i) for i in (1, 2, 3)])

    assert stats == {"requested": 3, "created": 2, "updated": 0, "failed": 1}


# ------------------------------------------------------------------- the caches


def test_forgetting_cached_rows_keeps_genres_ready() -> None:
    """Genres are committed by _ensure_genres, so they survive and are re-found.

    Re-fetching them from TMDb after every blip would be wasted calls.
    """
    service = _service(FakeSession())
    service._genres_ready = True
    service._genre_cache[18] = object()  # type: ignore[assignment]
    service._person_cache[501] = object()  # type: ignore[assignment]
    service._keyword_cache[1] = object()  # type: ignore[assignment]

    service._forget_cached_rows()

    assert service._genre_cache == {}
    assert service._person_cache == {}
    assert service._keyword_cache == {}
    assert service._genres_ready is True


@pytest.mark.asyncio
async def test_resetting_survives_a_rollback_that_itself_fails() -> None:
    """On a truly dead socket even the rollback throws; that must not mask it."""

    class RollbackFails(FakeSession):
        async def rollback(self) -> None:
            raise _dropped_socket()

    service = _service(RollbackFails())
    service._genre_cache[18] = object()  # type: ignore[assignment]

    await service._reset_session()

    assert service._genre_cache == {}
