"""Adding a title the catalog lacks: TMDb search, import, then rate it.

Search used to look only at titles ingested ahead of time, so a film missing
from the discover pages could not be found or used to shape a taste profile.
TMDb is faked here; the database and the ingest code are real.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.api.routes import titles as titles_routes
from app.application import title_import
from tests.integration.test_catalog_ingest import FakeTmdb

pytestmark = pytest.mark.integration

PASSWORD = "secure-pass-123"
MISSING_MOVIE = 9_400_001
MISSING_SHOW = 9_400_002


class SearchableTmdb(FakeTmdb):
    instances: list[SearchableTmdb] = []

    def __init__(self, settings: Any = None) -> None:
        super().__init__()
        SearchableTmdb.instances.append(self)

    async def search_titles(self, query: str) -> list[dict[str, Any]]:
        return [
            {
                "id": MISSING_MOVIE,
                "media_type": "movie",
                "title": "The Quiet Harbor",
                "release_date": "2011-03-04",
                "poster_path": "/qh.jpg",
                "overview": "A lighthouse keeper.",
            },
            {"id": MISSING_SHOW, "media_type": "tv", "name": "Harbor Lights", "first_air_date": ""},
            {"id": 77, "media_type": "person", "name": "Someone Famous"},
        ]

    async def get_tv(self, tmdb_id: int) -> dict[str, Any]:
        movie = self._movie(tmdb_id)
        return {
            **movie,
            "name": "Harbor Lights",
            "first_air_date": "2019-01-01",
            "episode_run_time": [45],
        }

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def fake_tmdb(monkeypatch: pytest.MonkeyPatch) -> None:
    SearchableTmdb.instances = []
    monkeypatch.setattr(titles_routes, "TmdbClient", SearchableTmdb)


async def _user(client: AsyncClient, api: str) -> dict[str, str]:
    email = f"imp_{uuid4().hex[:10]}@example.com"
    res = await client.post(f"{api}/auth/register", json={"email": email, "password": PASSWORD})
    assert res.status_code == 201, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


async def test_a_missing_film_can_be_found_added_and_rated(
    client: AsyncClient, api_prefix: str
) -> None:
    headers = await _user(client, api_prefix)

    local = await client.get(f"{api_prefix}/titles/search", params={"q": "Quiet Harbor"}, headers=headers)
    assert local.status_code == 200
    assert local.json() == []

    found = await client.get(
        f"{api_prefix}/titles/external-search", params={"q": "Quiet Harbor"}, headers=headers
    )
    assert found.status_code == 200, found.text
    rows = found.json()
    # People are not something to watch.
    assert [(r["tmdb_id"], r["media_type"]) for r in rows] == [
        (MISSING_MOVIE, "movie"),
        (MISSING_SHOW, "tv"),
    ]
    assert rows[0]["year"] == 2011
    assert rows[0]["poster_url"].endswith("/qh.jpg")
    assert rows[0]["title_id"] is None

    added = await client.post(
        f"{api_prefix}/titles/import",
        json={"media_type": "movie", "tmdb_id": MISSING_MOVIE},
        headers=headers,
    )
    assert added.status_code == 200, added.text
    title_id = added.json()["id"]

    detail = await client.get(f"{api_prefix}/titles/{title_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["name"] == f"Movie {MISSING_MOVIE}"

    rated = await client.post(
        f"{api_prefix}/titles/{title_id}/interactions",
        json={"event_type": "rate_4"},
        headers=headers,
    )
    assert rated.status_code == 204

    # Now it is in the catalog: search says so, and importing again is free.
    again = await client.get(
        f"{api_prefix}/titles/external-search", params={"q": "Quiet Harbor"}, headers=headers
    )
    assert again.json()[0]["title_id"] == title_id
    calls_before = sum(t.detail_calls for t in SearchableTmdb.instances)
    repeat = await client.post(
        f"{api_prefix}/titles/import",
        json={"media_type": "movie", "tmdb_id": MISSING_MOVIE},
        headers=headers,
    )
    assert repeat.json()["id"] == title_id
    assert sum(t.detail_calls for t in SearchableTmdb.instances) == calls_before


async def test_a_tv_show_can_be_added(client: AsyncClient, api_prefix: str) -> None:
    headers = await _user(client, api_prefix)
    added = await client.post(
        f"{api_prefix}/titles/import",
        json={"media_type": "tv", "tmdb_id": MISSING_SHOW},
        headers=headers,
    )
    assert added.status_code == 200, added.text
    detail = await client.get(f"{api_prefix}/titles/{added.json()['id']}", headers=headers)
    assert detail.json()["media_type"] == "tv"


async def test_the_same_tmdb_id_as_another_media_type_is_refused(
    client: AsyncClient, api_prefix: str
) -> None:
    headers = await _user(client, api_prefix)
    tmdb_id = 9_400_003
    first = await client.post(
        f"{api_prefix}/titles/import", json={"media_type": "movie", "tmdb_id": tmdb_id}, headers=headers
    )
    assert first.status_code == 200
    clash = await client.post(
        f"{api_prefix}/titles/import", json={"media_type": "tv", "tmdb_id": tmdb_id}, headers=headers
    )
    assert clash.status_code == 409
    assert clash.json()["code"] == "title_import_conflict"


async def test_imports_are_capped_per_user(
    client: AsyncClient, api_prefix: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(title_import, "IMPORTS_PER_HOUR", 2)
    headers = await _user(client, api_prefix)
    statuses = []
    for offset in range(3):
        res = await client.post(
            f"{api_prefix}/titles/import",
            json={"media_type": "movie", "tmdb_id": 9_400_100 + offset},
            headers=headers,
        )
        statuses.append(res.status_code)
    assert statuses == [200, 200, 429]


async def test_import_rejects_bad_input(client: AsyncClient, api_prefix: str) -> None:
    headers = await _user(client, api_prefix)
    for body in (
        {"media_type": "person", "tmdb_id": 1},
        {"media_type": "movie", "tmdb_id": 0},
        {"media_type": "movie", "tmdb_id": 1, "extra": True},
    ):
        res = await client.post(f"{api_prefix}/titles/import", json=body, headers=headers)
        assert res.status_code == 422, body
