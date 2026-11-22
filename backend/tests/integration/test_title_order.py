"""A title reads the same on every request, its main genre first.

Without an ORDER BY, Postgres returns a title's join rows in whatever order its
plan produces. A cached guest slate, re-read by a different query, once listed
one film's genres the other way round; the detail page keeps 20 of up to 25
keywords, so an unordered list also changed *which* keywords it showed. Cards
show the first genre (search) or the first three (For You), so the main genre,
which TMDb lists first and the feature snapshot keeps, leads.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.catalog import Genre, Keyword, Title, TitleGenre, TitleKeyword

pytestmark = pytest.mark.integration


async def _title(
    session: AsyncSession, tmdb_id: int, extra: dict[str, Any] | None, genres: list[Genre]
) -> Title:
    title = Title(
        id=uuid4(), external_tmdb_id=tmdb_id, media_type="movie", name="Order Film", extra=extra
    )
    session.add(title)
    await session.flush()
    # Linked in reverse alphabetical order: what an unordered read would hand back.
    session.add_all([TitleGenre(title_id=title.id, genre_id=g.id) for g in genres])
    return title


async def test_titles_list_the_main_genre_first_then_by_name(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    genres = [
        Genre(id=uuid4(), name=name, external_tmdb_id=9_600_100 + i)
        for i, name in enumerate(("Thriller", "Mystery", "Action"))
    ]
    keywords = [
        Keyword(id=uuid4(), name=name, external_tmdb_id=9_600_200 + i)
        for i, name in enumerate(("zeppelin", "heist", "alibi"))
    ]
    db_session.add_all([*genres, *keywords])
    await db_session.flush()

    # No feature snapshot (a row stored before ingest kept one): by name.
    plain = await _title(db_session, 9_600_001, None, genres)
    db_session.add_all([TitleKeyword(title_id=plain.id, keyword_id=k.id) for k in keywords])
    # TMDb listed Thriller first, so the snapshot weighs it most.
    snapshot = {"genre:thriller": 1.0, "genre:mystery": 0.7, "genre:action": 0.7}
    tmdb = await _title(db_session, 9_600_002, {"feature_snapshot": snapshot}, genres)
    await db_session.commit()

    res = await client.post(
        f"{api_prefix}/auth/register",
        json={"email": f"order-{uuid4().hex[:8]}@example.com", "password": "a-long-password-1"},
    )
    assert res.status_code == 201, res.text
    headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

    async def detail(title: Title) -> dict[str, Any]:
        response = await client.get(f"{api_prefix}/titles/{title.id}", headers=headers)
        assert response.status_code == 200, response.text
        return response.json()

    plain_body = await detail(plain)
    assert [g["name"] for g in plain_body["genres"]] == ["Action", "Mystery", "Thriller"]
    assert plain_body["keywords"] == ["alibi", "heist", "zeppelin"]

    tmdb_body = await detail(tmdb)
    assert [g["name"] for g in tmdb_body["genres"]] == ["Thriller", "Action", "Mystery"]
