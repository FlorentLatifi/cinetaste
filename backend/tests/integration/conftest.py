"""Fixtures for API integration tests (real Postgres + pgvector).

* The schema is built with ``alembic upgrade head`` — the migrations are part of
  what is being tested, not ``Base.metadata.create_all``.
* All integration tests share one event loop. The app's engine is a module-level
  singleton; with a fresh loop per test, pooled connections belong to a closed
  loop and every test after the first fails to connect.
* When Postgres is unreachable the tests are skipped locally, but fail when
  ``INTEGRATION_REQUIRED=1`` (set in CI) so a broken setup can't pass silently.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.infrastructure.db import models as _models  # noqa: F401 — register metadata
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.catalog import Genre, Title, TitleGenre
from app.infrastructure.db.session import async_session_factory, engine
from app.main import app
from app.recommendation.embeddings import PersonSignal, build_title_signals

API = get_settings().api_prefix
BACKEND_DIR = Path(__file__).resolve().parents[2]


def _unavailable(reason: str) -> None:
    if os.environ.get("INTEGRATION_REQUIRED") == "1":
        pytest.fail(reason)
    pytest.skip(reason)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    session_loop = pytest.mark.asyncio(loop_scope="session")
    for item in items:
        if "integration" in Path(str(item.fspath)).parts and pytest_asyncio.is_async_test(item):
            item.add_marker(session_loop, append=False)


@pytest.fixture(scope="session")
def migrated_database() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        _unavailable(f"alembic upgrade head failed: {result.stderr[-800:]}")


async def _reset_cache() -> None:
    from app.infrastructure.cache import reset_cache_for_tests

    await reset_cache_for_tests()


@pytest_asyncio.fixture(loop_scope="session")
async def integration_ready(migrated_database: None) -> AsyncIterator[None]:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        _unavailable(f"postgres unavailable: {exc}")

    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
    await _reset_cache()
    yield


@pytest_asyncio.fixture(loop_scope="session")
async def db_session(integration_ready: None) -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture(loop_scope="session")
async def client(integration_ready: None) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def seed_catalog(session: AsyncSession, *, count: int = 24) -> list[Title]:
    """Insert diverse movies with embeddings/posters for onboarding + recs."""
    genres_spec = [
        "Thriller",
        "Comedy",
        "Drama",
        "Science Fiction",
        "Action",
        "Horror",
    ]
    genre_rows: dict[str, Genre] = {}
    for i, name in enumerate(genres_spec):
        g = Genre(id=uuid4(), name=name, external_tmdb_id=9100 + i)
        session.add(g)
        genre_rows[name] = g
    await session.flush()

    titles: list[Title] = []
    for i in range(count):
        gname = genres_spec[i % len(genres_spec)]
        genres = [gname]
        if i % 3 == 0 and gname != "Drama":
            genres = [gname, "Drama"]
        name = f"Integration Film {i:02d}"
        year = 1990 + (i % 30)
        if gname == "Thriller":
            keywords = ["detective", "neo-noir"]
        elif gname == "Comedy":
            keywords = ["feel-good"]
        else:
            keywords = ["mind-bending"]
        people = [PersonSignal(name=f"Director {gname}", role="director")]
        emb, _feat, meta = build_title_signals(
            name=name,
            overview=f"Synopsis for {name} about {gname.lower()}.",
            genres=genres,
            keywords=keywords,
            people=people,
            media_type="movie",
            release_year=year,
            runtime=100 + i,
            popularity=float(80 - i),
            vote_average=6.5 + (i % 3) * 0.5,
            original_language="en",
            countries=["US"],
        )
        title = Title(
            id=uuid4(),
            media_type="movie",
            name=name,
            original_name=name,
            overview=f"Synopsis for {name}.",
            release_date=date(year, 6, 1),
            runtime=100 + i,
            popularity=float(80 - i),
            vote_average=6.5 + (i % 3) * 0.5,
            vote_count=1000 + i * 10,
            poster_path=f"/poster_{i}.jpg",
            original_language="en",
            external_tmdb_id=800_000 + i,
            embedding=emb,
            extra=meta,
        )
        session.add(title)
        await session.flush()
        for gn in genres:
            session.add(TitleGenre(title_id=title.id, genre_id=genre_rows[gn].id))
        titles.append(title)

    await session.commit()
    return titles


@pytest.fixture
def api_prefix() -> str:
    return API
