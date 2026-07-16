"""Fixtures for API integration tests.

Requires:
  - Postgres with pgvector (DATABASE_URL)
  - Redis (REDIS_URL)

When services are down (typical local laptop without Docker), tests are skipped
so pure unit suites stay green.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
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
from app.infrastructure.db.redis import close_redis, get_redis
from app.infrastructure.db.session import async_session_factory, engine
from app.main import app
from app.recommendation.embeddings import PersonSignal, build_title_signals

API = get_settings().api_prefix


async def _services_available() -> tuple[bool, str]:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return False, f"postgres unavailable: {exc}"
    try:
        redis = await get_redis()
        if not await redis.ping():
            return False, "redis ping failed"
    except Exception as exc:  # noqa: BLE001
        return False, f"redis unavailable: {exc}"
    return True, "ok"


@pytest_asyncio.fixture
async def integration_ready() -> AsyncIterator[None]:
    ok, reason = await _services_available()
    if not ok:
        pytest.skip(reason)

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    # Truncate so tests do not pollute each other (FK-safe order via metadata).
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())

    try:
        redis = await get_redis()
        await redis.flushdb()
    except Exception:
        pass

    yield


@pytest_asyncio.fixture
async def db_session(integration_ready: None) -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture
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
