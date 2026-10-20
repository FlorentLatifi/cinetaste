"""The detail page knows what you already did with a title.

It used to offer "Save" on a film already in your watchlist and "Rate" on one
you had rated, because the endpoint returned the catalog entry only.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.conftest import seed_catalog

pytestmark = pytest.mark.integration


async def _signed_in(client: AsyncClient, api: str) -> dict[str, str]:
    res = await client.post(
        f"{api}/auth/register",
        json={"email": f"detail-{uuid4().hex[:8]}@example.com", "password": "a-long-password-1"},
    )
    assert res.status_code == 201, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


async def _status(client: AsyncClient, api: str, headers, title_id) -> tuple[str | None, str | None]:
    body = (await client.get(f"{api}/titles/{title_id}", headers=headers)).json()
    return body["my_state"], body["my_rating"]


async def _act(client: AsyncClient, api: str, headers, title_id, event: str) -> None:
    res = await client.post(
        f"{api}/titles/{title_id}/interactions", json={"event_type": event}, headers=headers
    )
    assert res.status_code == 204, res.text


async def test_detail_reports_the_viewers_state_and_exact_rating(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    titles = await seed_catalog(db_session, count=6)
    headers = await _signed_in(client, api_prefix)
    title_id = titles[0].id

    assert await _status(client, api_prefix, headers, title_id) == (None, None)

    await _act(client, api_prefix, headers, title_id, "watchlist")
    assert await _status(client, api_prefix, headers, title_id) == ("watchlist", None)

    # "like" covers both Really liked and Loved; the page needs which one.
    await _act(client, api_prefix, headers, title_id, "rate_4")
    assert await _status(client, api_prefix, headers, title_id) == ("like", "rate_4")

    await _act(client, api_prefix, headers, title_id, "rate_1")
    assert await _status(client, api_prefix, headers, title_id) == ("dislike", "rate_1")

    await _act(client, api_prefix, headers, title_id, "clear")
    assert await _status(client, api_prefix, headers, title_id) == (None, None)

    await _act(client, api_prefix, headers, title_id, "not_interested")
    assert await _status(client, api_prefix, headers, title_id) == ("not_interested", None)


async def test_one_viewers_state_is_not_shown_to_another(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    titles = await seed_catalog(db_session, count=6)
    alice = await _signed_in(client, api_prefix)
    bob = await _signed_in(client, api_prefix)

    await _act(client, api_prefix, alice, titles[0].id, "rate_4")
    assert await _status(client, api_prefix, alice, titles[0].id) == ("like", "rate_4")
    assert await _status(client, api_prefix, bob, titles[0].id) == (None, None)
